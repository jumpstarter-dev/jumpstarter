//! Router stream-token authentication, mirroring
//! `RouterService.authenticate` (`controller/internal/service/router_service.go:52-78`)
//! plus the bearer extraction it delegates to
//! (`controller/internal/authentication/bearer.go:32-62`).
//!
//! The token contract (specs/rust-core/02-grpc-protocol.md §6.2): an HS256
//! JWT minted by `ControllerService.Dial`, signed with the raw bytes of env
//! `ROUTER_KEY`, claims `iss = "https://jumpstarter.dev/stream"`,
//! `aud = ["https://jumpstarter.dev/router"]`, `exp` required, and `sub` =
//! the stream/rendezvous id. Validation reproduces golang-jwt v5 with the
//! exact option set the Go router passes:
//!
//! - `jwt.WithValidMethods(HS256/HS384/HS512)` — other algorithms rejected;
//! - `jwt.WithIssuer` / `jwt.WithAudience` — exact match, claim required;
//! - `jwt.WithExpirationRequired` — a missing `exp` is fatal; `now < exp`
//!   must hold (golang-jwt `verifyExpiresAt` uses `now.Before(exp)`, so
//!   `exp == now` is already expired);
//! - `nbf` is validated whenever present (golang-jwt validates it by
//!   default, required=false): `now >= nbf` must hold;
//! - `jwt.WithIssuedAt` — **`iat` is validated only if present** (golang-jwt
//!   v5 calls `verifyIssuedAt(claims, now, required=false)`): a token
//!   without `iat` MUST be accepted; a future `iat` is rejected;
//! - zero leeway everywhere (the Go router configures none).
//!
//! ANY JWT validation failure collapses to gRPC
//! `INVALID_ARGUMENT "invalid jwt token"` (`router_service.go:73-74`).
//! Bearer-extraction failures keep their own Go statuses (`bearer.go:32-62`):
//! `UNAUTHENTICATED "missing authorization header"`,
//! `INVALID_ARGUMENT "multiple authorization headers"`,
//! `INVALID_ARGUMENT "malformed authorization header"`. (Go's
//! `INVALID_ARGUMENT "missing metadata"` case cannot occur under tonic,
//! which always materializes a metadata map.)
//!
//! Token expiry is checked at stream **admission only**: an already-paired
//! stream is never torn down when its token expires (spec 06 §3.2 timers).

use serde::Deserialize;
use std::time::{SystemTime, UNIX_EPOCH};
use thiserror::Error;
use tonic::metadata::MetadataMap;
use tonic::Status;

/// `iss` required on router stream tokens (`router_service.go:62`).
pub const STREAM_ISSUER: &str = "https://jumpstarter.dev/stream";
/// `aud` required on router stream tokens (`router_service.go:63`).
pub const ROUTER_AUDIENCE: &str = "https://jumpstarter.dev/router";

/// Where the HMAC key comes from. The Go router reads `os.Getenv("ROUTER_KEY")`
/// inside the JWT keyfunc — i.e. **per authentication attempt**, not once at
/// startup (`router_service.go:61`); [`KeySource::Env`] reproduces that.
/// [`KeySource::Static`] exists for tests (process-environment mutation is
/// racy under a parallel test runner).
#[derive(Debug, Clone)]
pub enum KeySource {
    /// Read the raw bytes of env `ROUTER_KEY` on every attempt; unset resolves
    /// to the empty key, exactly like Go's `[]byte(os.Getenv(...))`.
    Env,
    /// A fixed key (tests).
    Static(Vec<u8>),
}

impl KeySource {
    /// Resolve the current HMAC key bytes.
    pub fn key_bytes(&self) -> Vec<u8> {
        match self {
            KeySource::Env => std::env::var(jumpstarter_controller_config::env::ROUTER_KEY)
                .unwrap_or_default()
                .into_bytes(),
            KeySource::Static(key) => key.clone(),
        }
    }
}

/// Internal token-validation failure taxonomy. Wire-invisible (everything
/// maps to `INVALID_ARGUMENT "invalid jwt token"`); kept for debug logging.
#[derive(Debug, Error)]
pub enum TokenError {
    /// Signature/structure/algorithm failure from the JWT library.
    #[error("token verification failed: {0}")]
    Verification(#[from] jsonwebtoken::errors::Error),
    /// `jwt.WithExpirationRequired`: `exp` claim missing.
    #[error("exp claim is required")]
    MissingExpiration,
    /// `verifyExpiresAt`: `now < exp` does not hold.
    #[error("token is expired")]
    Expired,
    /// `verifyIssuedAt` (only when `iat` present): `now >= iat` does not hold.
    #[error("token used before issued")]
    UsedBeforeIssued,
    /// `verifyNotBefore` (only when `nbf` present): `now >= nbf` does not hold.
    #[error("token is not valid yet")]
    NotValidYet,
    /// `jwt.WithIssuer`: `iss` missing or not the expected value.
    #[error("token has invalid issuer")]
    InvalidIssuer,
    /// `jwt.WithAudience`: `aud` missing or not containing the expected value.
    #[error("token has invalid audience")]
    InvalidAudience,
}

/// `aud` may be a single string or an array of strings (golang-jwt
/// `ClaimStrings`); any other JSON shape fails deserialization, which — like
/// Go's claims-decode failure — collapses to an invalid token.
#[derive(Debug, Deserialize)]
#[serde(untagged)]
enum Audience {
    One(String),
    Many(Vec<String>),
}

impl Audience {
    fn contains(&self, expected: &str) -> bool {
        match self {
            Audience::One(aud) => aud == expected,
            Audience::Many(auds) => auds.iter().any(|aud| aud == expected),
        }
    }
}

/// The registered claims the router consumes (golang-jwt `RegisteredClaims`
/// subset). Numeric dates are JSON numbers in seconds (fractions allowed).
#[derive(Debug, Deserialize)]
struct RouterClaims {
    #[serde(default)]
    iss: Option<String>,
    #[serde(default)]
    sub: Option<String>,
    #[serde(default)]
    aud: Option<Audience>,
    #[serde(default)]
    exp: Option<f64>,
    #[serde(default)]
    nbf: Option<f64>,
    #[serde(default)]
    iat: Option<f64>,
}

/// Extracts the bearer token, porting `BearerTokenFromContext`
/// (`controller/internal/authentication/bearer.go:32-62`) including its
/// status codes and message strings.
// `tonic::Status` errors are the RPC-layer convention (workspace precedent:
// jumpstarter-driver-core); boxing would churn every call site for nothing.
#[allow(clippy::result_large_err)]
pub fn bearer_token_from_metadata(metadata: &MetadataMap) -> Result<String, Status> {
    let values: Vec<_> = metadata.get_all("authorization").iter().collect();

    if values.is_empty() {
        return Err(Status::unauthenticated("missing authorization header"));
    }

    // RFC 7230 §3.2.2: a sender MUST NOT generate multiple header fields
    // with the same field name (bearer.go:44-48).
    if values.len() > 1 {
        return Err(Status::invalid_argument("multiple authorization headers"));
    }

    let authorization = values[0]
        .to_str()
        .map_err(|_| Status::invalid_argument("malformed authorization header"))?;

    // Go: len < 7 || !strings.EqualFold(authorization[:7], "Bearer ").
    if authorization.len() < 7 || !authorization.as_bytes()[..7].eq_ignore_ascii_case(b"Bearer ") {
        return Err(Status::invalid_argument("malformed authorization header"));
    }

    Ok(authorization[7..].to_string())
}

/// Validates a router stream token against `key` at time `now` (seconds
/// since the Unix epoch) and returns the JWT `sub` — the rendezvous key.
/// A missing `sub` yields the empty string, exactly like golang-jwt's
/// `Claims.GetSubject()` on a sub-less token.
///
/// Signature/algorithm checking is delegated to `jsonwebtoken`; all
/// registered-claim checks are performed manually below so the accept/reject
/// boundaries match golang-jwt v5 exactly (see module docs) — in particular
/// `jsonwebtoken`'s built-in validation has a 60s default leeway and accepts
/// `exp == now`, both of which would diverge.
pub fn validate_token(token: &str, key: &[u8], now: f64) -> Result<String, TokenError> {
    use jsonwebtoken::{Algorithm, DecodingKey, Validation};

    let mut validation = Validation::new(Algorithm::HS256);
    // jwt.WithValidMethods([]string{HS256, HS384, HS512}) (router_service.go:66-70).
    validation.algorithms = vec![Algorithm::HS256, Algorithm::HS384, Algorithm::HS512];
    // Claim validation is manual (Go-exact); disable the library's.
    validation.validate_exp = false;
    validation.validate_nbf = false;
    validation.validate_aud = false;
    validation.required_spec_claims.clear();
    validation.leeway = 0;

    let data =
        jsonwebtoken::decode::<RouterClaims>(token, &DecodingKey::from_secret(key), &validation)?;
    let claims = data.claims;

    // golang-jwt v5 Validator.Validate order: exp, iat, nbf, aud, iss, sub.
    // (Order is wire-invisible here — every failure is the same status.)
    let exp = claims.exp.ok_or(TokenError::MissingExpiration)?;
    if now >= exp {
        // verifyExpiresAt: valid iff now.Before(exp).
        return Err(TokenError::Expired);
    }
    if let Some(iat) = claims.iat {
        // WithIssuedAt: validated only when present; valid iff !now.Before(iat).
        if now < iat {
            return Err(TokenError::UsedBeforeIssued);
        }
    }
    if let Some(nbf) = claims.nbf {
        // verifyNotBefore (always on, required=false): valid iff !now.Before(nbf).
        if now < nbf {
            return Err(TokenError::NotValidYet);
        }
    }
    match claims.iss.as_deref() {
        Some(STREAM_ISSUER) => {}
        _ => return Err(TokenError::InvalidIssuer),
    }
    match claims.aud {
        Some(ref aud) if aud.contains(ROUTER_AUDIENCE) => {}
        _ => return Err(TokenError::InvalidAudience),
    }

    Ok(claims.sub.unwrap_or_default())
}

/// Full authentication of an incoming `Stream` RPC: bearer extraction (Go
/// statuses preserved) then token validation, with **any** JWT failure
/// collapsing to `INVALID_ARGUMENT "invalid jwt token"`
/// (`router_service.go:73-74`). Returns the stream/rendezvous name.
#[allow(clippy::result_large_err)]
pub fn authenticate(metadata: &MetadataMap, key: &KeySource) -> Result<String, Status> {
    let token = bearer_token_from_metadata(metadata)?;
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64();
    validate_token(&token, &key.key_bytes(), now).map_err(|err| {
        tracing::debug!(error = %err, "router token validation failed");
        Status::invalid_argument("invalid jwt token")
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use jsonwebtoken::{Algorithm, EncodingKey, Header};
    use tonic::metadata::MetadataValue;

    const KEY: &[u8] = b"test-router-key";
    const NOW: f64 = 1_750_000_000.0;

    fn mint_with(alg: Algorithm, key: &[u8], claims: serde_json::Value) -> String {
        jsonwebtoken::encode(&Header::new(alg), &claims, &EncodingKey::from_secret(key))
            .expect("encode token")
    }

    fn standard_claims() -> serde_json::Value {
        serde_json::json!({
            "iss": STREAM_ISSUER,
            "sub": "stream-uuid-1",
            "aud": [ROUTER_AUDIENCE],
            "exp": NOW + 1800.0,
            "nbf": NOW,
            "iat": NOW,
            "jti": "some-jti",
        })
    }

    fn mint(claims: serde_json::Value) -> String {
        mint_with(Algorithm::HS256, KEY, claims)
    }

    #[test]
    fn valid_token_returns_subject() {
        let sub = validate_token(&mint(standard_claims()), KEY, NOW).unwrap();
        assert_eq!(sub, "stream-uuid-1");
    }

    #[test]
    fn hs384_and_hs512_are_accepted() {
        for alg in [Algorithm::HS384, Algorithm::HS512] {
            let token = mint_with(alg, KEY, standard_claims());
            assert_eq!(
                validate_token(&token, KEY, NOW).unwrap(),
                "stream-uuid-1",
                "alg {alg:?}"
            );
        }
    }

    /// The load-bearing golang-jwt v5 `WithIssuedAt` quirk: `iat` is
    /// validated only if present — an iat-less token MUST be accepted.
    #[test]
    fn token_without_iat_is_accepted() {
        let mut claims = standard_claims();
        claims.as_object_mut().unwrap().remove("iat");
        assert_eq!(
            validate_token(&mint(claims), KEY, NOW).unwrap(),
            "stream-uuid-1"
        );
    }

    #[test]
    fn future_iat_is_rejected() {
        let mut claims = standard_claims();
        claims["iat"] = serde_json::json!(NOW + 60.0);
        assert!(matches!(
            validate_token(&mint(claims), KEY, NOW),
            Err(TokenError::UsedBeforeIssued)
        ));
    }

    #[test]
    fn future_nbf_is_rejected_but_missing_nbf_is_fine() {
        let mut claims = standard_claims();
        claims["nbf"] = serde_json::json!(NOW + 60.0);
        assert!(matches!(
            validate_token(&mint(claims), KEY, NOW),
            Err(TokenError::NotValidYet)
        ));

        let mut claims = standard_claims();
        claims.as_object_mut().unwrap().remove("nbf");
        assert!(validate_token(&mint(claims), KEY, NOW).is_ok());
    }

    #[test]
    fn missing_exp_is_rejected() {
        // jwt.WithExpirationRequired (router_service.go:65).
        let mut claims = standard_claims();
        claims.as_object_mut().unwrap().remove("exp");
        assert!(matches!(
            validate_token(&mint(claims), KEY, NOW),
            Err(TokenError::MissingExpiration)
        ));
    }

    #[test]
    fn expired_token_is_rejected_including_exact_boundary() {
        let mut claims = standard_claims();
        claims["exp"] = serde_json::json!(NOW - 1.0);
        assert!(matches!(
            validate_token(&mint(claims), KEY, NOW),
            Err(TokenError::Expired)
        ));

        // golang-jwt verifyExpiresAt is now.Before(exp): exp == now is expired.
        let mut claims = standard_claims();
        claims["exp"] = serde_json::json!(NOW);
        assert!(matches!(
            validate_token(&mint(claims), KEY, NOW),
            Err(TokenError::Expired)
        ));
    }

    #[test]
    fn wrong_issuer_or_missing_issuer_is_rejected() {
        let mut claims = standard_claims();
        claims["iss"] = serde_json::json!("https://evil.example.com");
        assert!(matches!(
            validate_token(&mint(claims), KEY, NOW),
            Err(TokenError::InvalidIssuer)
        ));

        let mut claims = standard_claims();
        claims.as_object_mut().unwrap().remove("iss");
        assert!(matches!(
            validate_token(&mint(claims), KEY, NOW),
            Err(TokenError::InvalidIssuer)
        ));
    }

    #[test]
    fn wrong_audience_or_missing_audience_is_rejected() {
        let mut claims = standard_claims();
        claims["aud"] = serde_json::json!(["https://other.example.com"]);
        assert!(matches!(
            validate_token(&mint(claims), KEY, NOW),
            Err(TokenError::InvalidAudience)
        ));

        let mut claims = standard_claims();
        claims.as_object_mut().unwrap().remove("aud");
        assert!(matches!(
            validate_token(&mint(claims), KEY, NOW),
            Err(TokenError::InvalidAudience)
        ));
    }

    #[test]
    fn audience_as_bare_string_is_accepted() {
        // golang-jwt ClaimStrings accepts a single string too.
        let mut claims = standard_claims();
        claims["aud"] = serde_json::json!(ROUTER_AUDIENCE);
        assert!(validate_token(&mint(claims), KEY, NOW).is_ok());
    }

    #[test]
    fn wrong_key_is_rejected() {
        let token = mint_with(Algorithm::HS256, b"other-key", standard_claims());
        assert!(matches!(
            validate_token(&token, KEY, NOW),
            Err(TokenError::Verification(_))
        ));
    }

    #[test]
    fn garbage_and_alg_none_tokens_are_rejected() {
        assert!(validate_token("not-a-jwt", KEY, NOW).is_err());
        assert!(validate_token("", KEY, NOW).is_err());

        // Hand-rolled alg=none token (no signature).
        use base64::Engine as _;
        let engine = base64::engine::general_purpose::URL_SAFE_NO_PAD;
        let header = engine.encode(r#"{"alg":"none","typ":"JWT"}"#);
        let payload = engine.encode(standard_claims().to_string());
        let token = format!("{header}.{payload}.");
        assert!(validate_token(&token, KEY, NOW).is_err());
    }

    #[test]
    fn missing_sub_resolves_to_empty_rendezvous_key() {
        // golang-jwt GetSubject returns "" for a sub-less token; the Go
        // router happily uses "" as the stream name.
        let mut claims = standard_claims();
        claims.as_object_mut().unwrap().remove("sub");
        assert_eq!(validate_token(&mint(claims), KEY, NOW).unwrap(), "");
    }

    #[test]
    fn bearer_extraction_statuses_match_go() {
        // Missing header: UNAUTHENTICATED (bearer.go:40-42).
        let metadata = MetadataMap::new();
        let err = bearer_token_from_metadata(&metadata).unwrap_err();
        assert_eq!(err.code(), tonic::Code::Unauthenticated);
        assert_eq!(err.message(), "missing authorization header");

        // Multiple headers: INVALID_ARGUMENT (bearer.go:46-48).
        let mut metadata = MetadataMap::new();
        metadata.append("authorization", MetadataValue::from_static("Bearer a"));
        metadata.append("authorization", MetadataValue::from_static("Bearer b"));
        let err = bearer_token_from_metadata(&metadata).unwrap_err();
        assert_eq!(err.code(), tonic::Code::InvalidArgument);
        assert_eq!(err.message(), "multiple authorization headers");

        // Malformed scheme: INVALID_ARGUMENT (bearer.go:54-56).
        for value in ["Basic dXNlcg==", "Bearer", "Bear er x", ""] {
            let mut metadata = MetadataMap::new();
            metadata.insert("authorization", value.parse().unwrap());
            let err = bearer_token_from_metadata(&metadata).unwrap_err();
            assert_eq!(err.code(), tonic::Code::InvalidArgument, "value {value:?}");
            assert_eq!(err.message(), "malformed authorization header");
        }

        // Go uses EqualFold: any case of "bearer " is accepted.
        for value in ["Bearer tok", "bearer tok", "BEARER tok", "BeArEr tok"] {
            let mut metadata = MetadataMap::new();
            metadata.insert("authorization", value.parse().unwrap());
            assert_eq!(bearer_token_from_metadata(&metadata).unwrap(), "tok");
        }
    }

    #[test]
    fn authenticate_collapses_jwt_failures_to_invalid_jwt_token() {
        let mut metadata = MetadataMap::new();
        metadata.insert("authorization", "Bearer garbage".parse().unwrap());
        let err = authenticate(&metadata, &KeySource::Static(KEY.to_vec())).unwrap_err();
        assert_eq!(err.code(), tonic::Code::InvalidArgument);
        assert_eq!(err.message(), "invalid jwt token");
    }
}
