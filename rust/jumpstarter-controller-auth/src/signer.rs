//! Internal ES256 token signer, port of `controller/internal/oidc/op.go`
//! (`Signer`) as constructed by `controller/cmd/main.go:212-216`.
//!
//! Key material resolution:
//! 1. `CONTROLLER_PRIVATE_KEY_PEM` (PKCS#8 or SEC1 EC PEM, P-256) — an
//!    approved **deliberate addition** over Go: the rotation/HSM escape hatch.
//!    Takes precedence whenever set to a non-empty value.
//! 2. Otherwise the Go-compatible deterministic derivation from the
//!    `CONTROLLER_KEY` seed (`op.go:37-46`): `sha256(seed)` → Go `math/rand`
//!    source → `keygen.ECDSALegacy(P256)`, provided by
//!    [`crate::go_compat::derive_key_from_seed`] and locked by golden vectors.
//!    Like Go, an unset `CONTROLLER_KEY` still derives (from the empty seed).
//!
//! Token issuance (`op.go:106-121`): ES256 JWT with claims
//! `iss` (= `https://localhost:8085`), `sub`, `aud` (= `["jumpstarter"]`,
//! kept an array like golang-jwt's `MarshalSingleStringAsArray` default),
//! `exp = now + lifetime`, `iat = now`; the default lifetime is
//! `365 * 24h` (`op.go:20`), overridable via `SetTokenLifetime` — wired from
//! config `authentication.internal.tokenLifetime`, which must parse as a
//! positive Go duration (`internal/config/oidc.go:29-38`).
//!
//! Validation (`op.go:93-104`): golang-jwt v5 `jwt.Parse` with
//! `WithValidMethods([ES256])`, `WithIssuer`, `WithAudience` and the default
//! validator set — i.e. `exp`/`nbf` are validated **only when present**
//! (neither is required), `iat` is **not** validated at all
//! (`jwt.WithIssuedAt` is not passed, unlike the router), and `iss`/`aud`
//! are required exact/contains matches. Zero leeway.
//!
//! JWKS (`op.go:56-74` + `zitadel/op.Keys`): a single go-jose JWK with
//! `use: "sig"`, `kty: "EC"`, `kid: "default"` (`ID()`), `crv: "P-256"`,
//! `alg: "ES256"` and fixed-width 32-byte base64url-no-pad `x`/`y`.

use std::time::{Duration, SystemTime, UNIX_EPOCH};

use base64::Engine as _;
use p256::ecdsa::signature::RandomizedSigner as _;
use p256::elliptic_curve::sec1::ToEncodedPoint;
use p256::pkcs8::EncodePrivateKey;
use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::go_compat;

/// Issuer of internal tokens (`cmd/main.go:214`).
pub const INTERNAL_ISSUER: &str = "https://localhost:8085";
/// Audience of internal tokens (`cmd/main.go:215`).
pub const INTERNAL_AUDIENCE: &str = "jumpstarter";
/// JWKS key id (`op.go:56-58` `ID()`).
pub const KEY_ID: &str = "default";
/// `defaultTokenLifetime` (`op.go:20`): 365 * 24h.
pub const DEFAULT_TOKEN_LIFETIME: Duration = Duration::from_secs(365 * 24 * 60 * 60);
/// Env var carrying a PEM private-key override (PKCS#8 or SEC1). Deliberate
/// addition over Go (rotation/HSM escape hatch); takes precedence over the
/// `CONTROLLER_KEY` derivation.
pub const ENV_CONTROLLER_PRIVATE_KEY_PEM: &str = "CONTROLLER_PRIVATE_KEY_PEM";

/// Errors constructing a [`Signer`] or signing a token. All are fatal at
/// startup in Go (`cmd/main.go:217-220`).
#[derive(Debug, Error)]
pub enum SignerError {
    /// `CONTROLLER_PRIVATE_KEY_PEM` was set but parses as neither PKCS#8
    /// (`PRIVATE KEY`) nor SEC1 (`EC PRIVATE KEY`) P-256 PEM.
    #[error(
        "invalid {ENV_CONTROLLER_PRIVATE_KEY_PEM}: not a P-256 private key \
         (PKCS#8: {pkcs8}; SEC1: {sec1})"
    )]
    InvalidPrivateKeyPem { pkcs8: String, sec1: String },
    /// The Go-compatible seed derivation failed (unreachable in practice,
    /// see [`go_compat::DeriveKeyError`]).
    #[error(transparent)]
    Derive(#[from] go_compat::DeriveKeyError),
    /// Re-encoding the secret key to PKCS#8 for jsonwebtoken failed
    /// (unreachable for keys this module constructs).
    #[error("failed to encode signing key: {reason}")]
    KeyEncoding { reason: String },
    /// jsonwebtoken rejected the key material or the signing operation.
    #[error("failed to sign token: {0}")]
    Jwt(#[from] jsonwebtoken::errors::Error),
}

/// Token-validation failure taxonomy for [`Signer::validate`], mirroring the
/// golang-jwt v5 checks `op.go:93-104` enables. Kept internal-facing (Go
/// callers only branch on valid/invalid; reconcilers re-sign on any error).
#[derive(Debug, Error)]
pub enum ValidateError {
    /// Signature/structure/algorithm failure from the JWT library
    /// (`WithValidMethods([ES256])`).
    #[error("token verification failed: {0}")]
    Verification(#[from] jsonwebtoken::errors::Error),
    /// `verifyExpiresAt` (only when `exp` present): `now < exp` must hold.
    #[error("token is expired")]
    Expired,
    /// `verifyNotBefore` (only when `nbf` present): `now >= nbf` must hold.
    #[error("token is not valid yet")]
    NotValidYet,
    /// `jwt.WithIssuer`: `iss` missing or not the expected value.
    #[error("token has invalid issuer")]
    InvalidIssuer,
    /// `jwt.WithAudience`: `aud` missing or not containing the expected value.
    #[error("token has invalid audience")]
    InvalidAudience,
}

/// Port of the Go `oidc.Signer`: the internal ES256 issuer/validator and the
/// key behind the JWKS document.
pub struct Signer {
    secret_key: p256::SecretKey,
    encoding_key: jsonwebtoken::EncodingKey,
    decoding_key: jsonwebtoken::DecodingKey,
    /// base64url-no-pad fixed-width public-key coordinates (JWK `x`/`y`).
    jwk_x: String,
    jwk_y: String,
    issuer: String,
    audience: String,
    /// `None` reproduces Go's zero `tokenLifetime`: the default is applied at
    /// issue time (`op.go:109-112`), not at construction.
    token_lifetime: Option<Duration>,
}

impl std::fmt::Debug for Signer {
    /// Manual impl: never expose the private scalar in logs.
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Signer")
            .field("issuer", &self.issuer)
            .field("audience", &self.audience)
            .field("token_lifetime", &self.token_lifetime)
            .field("jwk_x", &self.jwk_x)
            .field("jwk_y", &self.jwk_y)
            .finish_non_exhaustive()
    }
}

impl Signer {
    /// Port of `NewSigner` (`op.go:29-35`) — wraps an existing key.
    pub fn new(
        secret_key: p256::SecretKey,
        issuer: impl Into<String>,
        audience: impl Into<String>,
    ) -> Result<Self, SignerError> {
        // jsonwebtoken's ES256 signer wants PKCS#8; round-trip through PEM.
        let pkcs8 = secret_key
            .to_pkcs8_pem(p256::pkcs8::LineEnding::LF)
            .map_err(|err| SignerError::KeyEncoding {
                reason: err.to_string(),
            })?;
        let encoding_key = jsonwebtoken::EncodingKey::from_ec_pem(pkcs8.as_bytes())?;

        let point = secret_key.public_key().to_encoded_point(false);
        let engine = base64::engine::general_purpose::URL_SAFE_NO_PAD;
        // Fixed-width 32-byte coordinates, exactly like go-jose's
        // `newFixedSizeBuffer` (jwk.go `fromEcPublicKey`).
        let jwk_x = engine.encode(point.x().expect("uncompressed point has x"));
        let jwk_y = engine.encode(point.y().expect("uncompressed point has y"));
        let decoding_key = jsonwebtoken::DecodingKey::from_ec_components(&jwk_x, &jwk_y)?;

        Ok(Self {
            secret_key,
            encoding_key,
            decoding_key,
            jwk_x,
            jwk_y,
            issuer: issuer.into(),
            audience: audience.into(),
            token_lifetime: None,
        })
    }

    /// Port of `NewSignerFromSeed` (`op.go:37-46`): the deterministic
    /// Go-compatible key derivation from the `CONTROLLER_KEY` bytes.
    pub fn from_seed(
        seed: &[u8],
        issuer: impl Into<String>,
        audience: impl Into<String>,
    ) -> Result<Self, SignerError> {
        Self::new(go_compat::derive_key_from_seed(seed)?, issuer, audience)
    }

    /// Constructs a signer from a PEM private key — the
    /// `CONTROLLER_PRIVATE_KEY_PEM` override path. Accepts PKCS#8
    /// (`-----BEGIN PRIVATE KEY-----`) and SEC1
    /// (`-----BEGIN EC PRIVATE KEY-----`); the input is trimmed first so a
    /// trailing newline from `kubectl create secret`/env plumbing is fine.
    pub fn from_pem(
        pem: &str,
        issuer: impl Into<String>,
        audience: impl Into<String>,
    ) -> Result<Self, SignerError> {
        use p256::pkcs8::DecodePrivateKey;

        let pem = pem.trim();
        let pkcs8_err = match p256::SecretKey::from_pkcs8_pem(pem) {
            Ok(key) => return Self::new(key, issuer, audience),
            Err(err) => err.to_string(),
        };
        match p256::SecretKey::from_sec1_pem(pem) {
            Ok(key) => Self::new(key, issuer, audience),
            Err(err) => Err(SignerError::InvalidPrivateKeyPem {
                pkcs8: pkcs8_err,
                sec1: err.to_string(),
            }),
        }
    }

    /// Env-free core of [`Signer::from_env`]: `pem_override` (when `Some`)
    /// wins over the seed derivation.
    pub fn from_key_material(
        pem_override: Option<&str>,
        seed: &[u8],
        issuer: impl Into<String>,
        audience: impl Into<String>,
    ) -> Result<Self, SignerError> {
        match pem_override {
            Some(pem) => Self::from_pem(pem, issuer, audience),
            None => Self::from_seed(seed, issuer, audience),
        }
    }

    /// The production constructor, mirroring `cmd/main.go:212-216` plus the
    /// PEM override: `CONTROLLER_PRIVATE_KEY_PEM` (if non-empty) beats the
    /// `CONTROLLER_KEY` seed derivation; like Go, an unset `CONTROLLER_KEY`
    /// derives from the empty seed rather than failing.
    pub fn from_env() -> Result<Self, SignerError> {
        let pem = std::env::var(ENV_CONTROLLER_PRIVATE_KEY_PEM)
            .ok()
            .filter(|value| !value.trim().is_empty());
        let seed =
            std::env::var(jumpstarter_controller_config::env::CONTROLLER_KEY).unwrap_or_default();
        if pem.is_some() {
            tracing::info!(
                "internal signer key loaded from {ENV_CONTROLLER_PRIVATE_KEY_PEM} override"
            );
        }
        Self::from_key_material(
            pem.as_deref(),
            seed.as_bytes(),
            INTERNAL_ISSUER,
            INTERNAL_AUDIENCE,
        )
    }

    /// `Issuer()` (`op.go:48-50`).
    pub fn issuer(&self) -> &str {
        &self.issuer
    }

    /// `Audience()` (`op.go:52-54`).
    pub fn audience(&self) -> &str {
        &self.audience
    }

    /// `ID()` (`op.go:56-58`) — the JWKS `kid`.
    pub fn id(&self) -> &'static str {
        KEY_ID
    }

    /// The signer's public key (`Key()`, `op.go:68-70`).
    pub fn public_key(&self) -> p256::PublicKey {
        self.secret_key.public_key()
    }

    /// `SetTokenLifetime` (`op.go:89-91`), wired from config
    /// `authentication.internal.tokenLifetime` by the config loader
    /// (`internal/config/oidc.go:29-38` — which rejects non-positive values
    /// before ever calling this). Sub-second fractions are truncated at issue
    /// time (numeric dates are whole seconds).
    pub fn set_token_lifetime(&mut self, lifetime: Duration) {
        self.token_lifetime = Some(lifetime);
    }

    /// Effective lifetime: Go applies the default at issue time when the
    /// configured lifetime is zero (`op.go:109-112`).
    fn effective_lifetime(&self) -> Duration {
        match self.token_lifetime {
            Some(lifetime) if lifetime != Duration::ZERO => lifetime,
            _ => DEFAULT_TOKEN_LIFETIME,
        }
    }

    /// `Token(subject)` (`op.go:106-121`) at the current system time.
    pub fn token(&self, subject: &str) -> Result<String, SignerError> {
        self.token_at(subject, unix_now_secs())
    }

    /// `Token(subject)` with an explicit issue time (seconds since the Unix
    /// epoch, i.e. what golang-jwt's `NumericDate` truncates `time.Now()` to).
    pub fn token_at(&self, subject: &str, now_unix_secs: i64) -> Result<String, SignerError> {
        let claims = InternalClaims {
            iss: &self.issuer,
            sub: subject,
            aud: [&self.audience],
            exp: now_unix_secs + self.effective_lifetime().as_secs() as i64,
            iat: now_unix_secs,
        };
        // jsonwebtoken produces the canonical `b64(header).b64(claims).b64(sig)`
        // but with a *deterministic* (RFC 6979) ES256 signature, so two tokens
        // with identical claims (e.g. a rotation within the same second) are
        // byte-identical. Go signs with a fresh random nonce (crypto/ecdsa via
        // go-jose), so its tokens always differ. Reuse jsonwebtoken's exact
        // header+claims serialization (the signing input) but replace the
        // signature with a randomized one so rotation always yields a new token.
        let deterministic = jsonwebtoken::encode(
            &jsonwebtoken::Header::new(jsonwebtoken::Algorithm::ES256),
            &claims,
            &self.encoding_key,
        )?;
        let signing_input = deterministic
            .rsplit_once('.')
            .map(|(head, _sig)| head)
            .unwrap_or(&deterministic);
        let signing_key = p256::ecdsa::SigningKey::from(&self.secret_key);
        let signature: p256::ecdsa::Signature =
            signing_key.sign_with_rng(&mut rand_core::OsRng, signing_input.as_bytes());
        let sig_b64 = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .encode(signature.to_bytes());
        Ok(format!("{signing_input}.{sig_b64}"))
    }

    /// `Validate(token)` (`op.go:93-104`) at the current system time.
    pub fn validate(&self, token: &str) -> Result<(), ValidateError> {
        self.validate_at(token, unix_now_secs() as f64)
    }

    /// `Validate(token)` at an explicit time. Signature/algorithm checking is
    /// delegated to `jsonwebtoken`; registered-claim checks are manual so the
    /// accept/reject boundaries match golang-jwt v5 exactly (see module docs;
    /// same approach as `jumpstarter-router-service::auth`): `exp`/`nbf`
    /// optional-but-validated, `iat` **ignored**, `iss`/`aud` required, zero
    /// leeway, `now.Before(exp)` semantics (`exp == now` is already expired).
    pub fn validate_at(&self, token: &str, now: f64) -> Result<(), ValidateError> {
        use jsonwebtoken::{Algorithm, Validation};

        let mut validation = Validation::new(Algorithm::ES256);
        // Claim validation is manual (Go-exact); disable the library's.
        validation.validate_exp = false;
        validation.validate_nbf = false;
        validation.validate_aud = false;
        validation.required_spec_claims.clear();
        validation.leeway = 0;

        let data =
            jsonwebtoken::decode::<InternalTokenClaims>(token, &self.decoding_key, &validation)?;
        let claims = data.claims;

        if let Some(exp) = claims.exp {
            // verifyExpiresAt (required=false): valid iff now.Before(exp).
            if now >= exp {
                return Err(ValidateError::Expired);
            }
        }
        if let Some(nbf) = claims.nbf {
            // verifyNotBefore (required=false): valid iff !now.Before(nbf).
            if now < nbf {
                return Err(ValidateError::NotValidYet);
            }
        }
        // No iat check: op.go does not pass jwt.WithIssuedAt.
        match claims.aud {
            Some(ref aud) if aud.contains(&self.audience) => {}
            _ => return Err(ValidateError::InvalidAudience),
        }
        match claims.iss.as_deref() {
            Some(iss) if iss == self.issuer => {}
            _ => return Err(ValidateError::InvalidIssuer),
        }
        Ok(())
    }

    /// The single public JWK exactly as go-jose marshals it inside
    /// `op.Keys` (`zitadel op/keys.go` + go-jose `jwk.go` field order).
    pub fn jwk(&self) -> Jwk<'_> {
        Jwk {
            r#use: "sig",
            kty: "EC",
            kid: KEY_ID,
            crv: "P-256",
            alg: "ES256",
            x: &self.jwk_x,
            y: &self.jwk_y,
        }
    }

    /// The exact `GET /jwks` response body the Go signer serves
    /// (`op.go:84-86` → `op.Keys` → `httphelper.MarshalJSON`), including the
    /// trailing newline `json.Encoder.Encode` appends.
    pub fn jwks_document(&self) -> String {
        let mut body = serde_json::to_string(&JwkSet { keys: [self.jwk()] })
            .expect("JWKS document serialization cannot fail");
        body.push('\n');
        body
    }
}

/// Registered claims as issued by `Token()` — golang-jwt v5 marshals
/// `jwt.RegisteredClaims` in struct field order with `omitempty`, so the
/// wire order is `iss, sub, aud, exp, iat` (no `nbf`/`jti` on internal
/// tokens) and the single-element `aud` stays an array
/// (`MarshalSingleStringAsArray` defaults to true). Pinned byte-for-byte
/// against the Go-generated golden token in the tests below.
#[derive(Serialize)]
struct InternalClaims<'a> {
    iss: &'a str,
    sub: &'a str,
    aud: [&'a str; 1],
    exp: i64,
    iat: i64,
}

/// `aud` may be a single string or an array (golang-jwt `ClaimStrings`); any
/// other shape fails deserialization, which — like Go's claims-decode
/// failure — surfaces as an invalid token.
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

/// The registered claims [`Signer::validate_at`] consumes. Numeric dates are
/// JSON numbers in seconds (fractions allowed, like golang-jwt).
#[derive(Debug, Deserialize)]
struct InternalTokenClaims {
    #[serde(default)]
    iss: Option<String>,
    #[serde(default)]
    aud: Option<Audience>,
    #[serde(default)]
    exp: Option<f64>,
    #[serde(default)]
    nbf: Option<f64>,
}

/// go-jose `rawJSONWebKey` (jwk.go:42-67), restricted to the fields an EC
/// public signing key populates, **in go-jose's struct order** so the
/// serialized document is byte-identical to what Go serves.
#[derive(Debug, Serialize)]
pub struct Jwk<'a> {
    pub r#use: &'a str,
    pub kty: &'a str,
    pub kid: &'a str,
    pub crv: &'a str,
    pub alg: &'a str,
    pub x: &'a str,
    pub y: &'a str,
}

/// go-jose `JSONWebKeySet` (`{"keys":[...]}`).
#[derive(Serialize)]
struct JwkSet<'a> {
    keys: [Jwk<'a>; 1],
}

fn unix_now_secs() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW: i64 = 1_750_000_000;

    fn test_signer() -> Signer {
        Signer::from_seed(b"golden-controller-key", INTERNAL_ISSUER, INTERNAL_AUDIENCE)
            .expect("signer from seed")
    }

    fn payload_json(token: &str) -> String {
        let payload = token.split('.').nth(1).expect("three-segment JWT");
        let bytes = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .decode(payload)
            .expect("base64url payload");
        String::from_utf8(bytes).expect("utf8 payload")
    }

    fn golden_tokens() -> serde_json::Value {
        serde_json::from_str(include_str!("../tests/golden/tokens.json"))
            .expect("golden tokens.json parses")
    }

    #[test]
    fn round_trip_default_lifetime() {
        let signer = test_signer();
        let token = signer.token("client:default:sample:uid-1").expect("token");
        signer.validate(&token).expect("round-trip validates");
    }

    /// The exact claim set + JSON key order of `Token()`
    /// (`op.go:113-120`): `{"iss","sub","aud":[...],"exp","iat"}`.
    #[test]
    fn claim_set_pins_go_shape() {
        let signer = test_signer();
        let token = signer
            .token_at("client:default:golden:uid-1234", NOW)
            .unwrap();
        let expected = format!(
            r#"{{"iss":"{INTERNAL_ISSUER}","sub":"client:default:golden:uid-1234","aud":["{INTERNAL_AUDIENCE}"],"exp":{},"iat":{NOW}}}"#,
            NOW + 365 * 24 * 60 * 60,
        );
        assert_eq!(payload_json(&token), expected);
    }

    /// Byte-for-byte payload parity with the Go-minted golden token
    /// (tests/golden/tokens.json `internal`, exp +100y): re-minting at the
    /// golden `iat` with the golden lifetime must reproduce the exact
    /// payload segment Go signed.
    #[test]
    fn golden_internal_token_payload_parity() {
        let golden = golden_tokens();
        let internal = &golden["internal"];
        let claims = &internal["claims"];
        let iat = claims["iat"].as_i64().expect("golden iat");
        let exp = claims["exp"].as_i64().expect("golden exp");
        let subject = internal["subject"].as_str().expect("golden subject");
        let token = internal["token"].as_str().expect("golden token");

        let mut signer = test_signer();
        signer.set_token_lifetime(Duration::from_secs((exp - iat) as u64));
        let minted = signer.token_at(subject, iat).expect("mint at golden iat");

        assert_eq!(
            payload_json(&minted),
            payload_json(token),
            "Rust claims must be byte-identical to golang-jwt's"
        );
    }

    /// The Go-signed golden token must validate against the Rust signer
    /// derived from the same seed (Go→Rust cross-verification).
    #[test]
    fn golden_internal_token_validates() {
        let golden = golden_tokens();
        let internal = &golden["internal"];
        let seed = base64::engine::general_purpose::STANDARD
            .decode(internal["seed_b64"].as_str().expect("seed_b64"))
            .expect("golden seed decodes");
        let signer = Signer::from_seed(&seed, INTERNAL_ISSUER, INTERNAL_AUDIENCE).unwrap();
        signer
            .validate(internal["token"].as_str().expect("token"))
            .expect("Go-signed token validates in Rust");
    }

    #[test]
    fn lifetime_default_and_override() {
        let mut signer = test_signer();

        // Default: 365 * 24h (op.go:20).
        let token = signer.token_at("sub", NOW).unwrap();
        let claims: serde_json::Value = serde_json::from_str(&payload_json(&token)).unwrap();
        assert_eq!(
            claims["exp"].as_i64().unwrap() - claims["iat"].as_i64().unwrap(),
            365 * 24 * 60 * 60
        );

        // Config override (config/oidc.go:29-38 -> SetTokenLifetime).
        signer.set_token_lifetime(Duration::from_secs(3600));
        let token = signer.token_at("sub", NOW).unwrap();
        let claims: serde_json::Value = serde_json::from_str(&payload_json(&token)).unwrap();
        assert_eq!(
            claims["exp"].as_i64().unwrap() - claims["iat"].as_i64().unwrap(),
            3600
        );

        // Zero lifetime falls back to the default at issue time (op.go:109-112).
        signer.set_token_lifetime(Duration::ZERO);
        let token = signer.token_at("sub", NOW).unwrap();
        let claims: serde_json::Value = serde_json::from_str(&payload_json(&token)).unwrap();
        assert_eq!(
            claims["exp"].as_i64().unwrap() - claims["iat"].as_i64().unwrap(),
            365 * 24 * 60 * 60
        );
    }

    #[test]
    fn validate_rejects_expired_including_exact_boundary() {
        let mut signer = test_signer();
        signer.set_token_lifetime(Duration::from_secs(60));
        let token = signer.token_at("sub", NOW).unwrap();

        assert!(signer.validate_at(&token, (NOW + 30) as f64).is_ok());
        // golang-jwt verifyExpiresAt is now.Before(exp): exp == now is expired.
        assert!(matches!(
            signer.validate_at(&token, (NOW + 60) as f64),
            Err(ValidateError::Expired)
        ));
        assert!(matches!(
            signer.validate_at(&token, (NOW + 61) as f64),
            Err(ValidateError::Expired)
        ));
    }

    /// op.go passes neither WithExpirationRequired nor WithIssuedAt: a token
    /// without `exp` is accepted, and a future `iat` is NOT rejected (unlike
    /// the router validator).
    #[test]
    fn validate_go_quirks_exp_optional_iat_ignored() {
        let signer = test_signer();
        let header = jsonwebtoken::Header::new(jsonwebtoken::Algorithm::ES256);

        let no_exp = serde_json::json!({
            "iss": INTERNAL_ISSUER,
            "sub": "sub",
            "aud": [INTERNAL_AUDIENCE],
        });
        let token = jsonwebtoken::encode(&header, &no_exp, &signer.encoding_key).unwrap();
        signer
            .validate_at(&token, NOW as f64)
            .expect("exp is not required (no WithExpirationRequired)");

        let future_iat = serde_json::json!({
            "iss": INTERNAL_ISSUER,
            "sub": "sub",
            "aud": [INTERNAL_AUDIENCE],
            "exp": NOW + 3600,
            "iat": NOW + 3000,
        });
        let token = jsonwebtoken::encode(&header, &future_iat, &signer.encoding_key).unwrap();
        signer
            .validate_at(&token, NOW as f64)
            .expect("iat is not validated (no WithIssuedAt)");

        // nbf, however, is validated whenever present (default validator).
        let future_nbf = serde_json::json!({
            "iss": INTERNAL_ISSUER,
            "sub": "sub",
            "aud": [INTERNAL_AUDIENCE],
            "exp": NOW + 3600,
            "nbf": NOW + 3000,
        });
        let token = jsonwebtoken::encode(&header, &future_nbf, &signer.encoding_key).unwrap();
        assert!(matches!(
            signer.validate_at(&token, NOW as f64),
            Err(ValidateError::NotValidYet)
        ));
    }

    #[test]
    fn validate_rejects_wrong_issuer_audience_and_key() {
        let signer = test_signer();
        let header = jsonwebtoken::Header::new(jsonwebtoken::Algorithm::ES256);

        let wrong_iss = serde_json::json!({
            "iss": "https://evil.example.com",
            "sub": "sub",
            "aud": [INTERNAL_AUDIENCE],
        });
        let token = jsonwebtoken::encode(&header, &wrong_iss, &signer.encoding_key).unwrap();
        assert!(matches!(
            signer.validate_at(&token, NOW as f64),
            Err(ValidateError::InvalidIssuer)
        ));

        let wrong_aud = serde_json::json!({
            "iss": INTERNAL_ISSUER,
            "sub": "sub",
            "aud": ["not-jumpstarter"],
        });
        let token = jsonwebtoken::encode(&header, &wrong_aud, &signer.encoding_key).unwrap();
        assert!(matches!(
            signer.validate_at(&token, NOW as f64),
            Err(ValidateError::InvalidAudience)
        ));

        // Signed by a different key: signature failure.
        let other = Signer::from_seed(b"other-seed", INTERNAL_ISSUER, INTERNAL_AUDIENCE).unwrap();
        let token = other.token_at("sub", NOW).unwrap();
        assert!(matches!(
            signer.validate_at(&token, NOW as f64),
            Err(ValidateError::Verification(_))
        ));

        // HS256 token claiming our issuer: rejected (WithValidMethods).
        let hs = jsonwebtoken::encode(
            &jsonwebtoken::Header::new(jsonwebtoken::Algorithm::HS256),
            &serde_json::json!({"iss": INTERNAL_ISSUER, "aud": [INTERNAL_AUDIENCE]}),
            &jsonwebtoken::EncodingKey::from_secret(b"secret"),
        )
        .unwrap();
        assert!(matches!(
            signer.validate_at(&hs, NOW as f64),
            Err(ValidateError::Verification(_))
        ));
    }

    /// PEM override: both PKCS#8 and SEC1 encodings of the same key are
    /// accepted and produce the same signer key; the override beats the seed.
    #[test]
    fn pem_override_precedence_and_formats() {
        let derived = go_compat::derive_key_from_seed(b"seed-a").unwrap();
        let override_key = go_compat::derive_key_from_seed(b"seed-b").unwrap();

        let pkcs8 = override_key
            .to_pkcs8_pem(p256::pkcs8::LineEnding::LF)
            .unwrap()
            .to_string();
        let sec1 = override_key
            .to_sec1_pem(p256::pkcs8::LineEnding::LF)
            .unwrap()
            .to_string();

        for pem in [&pkcs8, &sec1] {
            let signer =
                Signer::from_key_material(Some(pem), b"seed-a", INTERNAL_ISSUER, INTERNAL_AUDIENCE)
                    .expect("override signer");
            assert_eq!(
                signer.public_key(),
                override_key.public_key(),
                "override key must win over the seed"
            );
            assert_ne!(signer.public_key(), derived.public_key());
        }

        // Trailing whitespace (typical of mounted Secrets) is tolerated.
        let signer = Signer::from_key_material(
            Some(&format!("{pkcs8}\n\n")),
            b"seed-a",
            INTERNAL_ISSUER,
            INTERNAL_AUDIENCE,
        )
        .expect("trailing-newline PEM");
        assert_eq!(signer.public_key(), override_key.public_key());

        // No override: the seed derivation is used.
        let signer =
            Signer::from_key_material(None, b"seed-a", INTERNAL_ISSUER, INTERNAL_AUDIENCE).unwrap();
        assert_eq!(signer.public_key(), derived.public_key());

        // Garbage override: loud error, no silent fallback to the seed.
        assert!(matches!(
            Signer::from_key_material(
                Some("not a pem"),
                b"seed-a",
                INTERNAL_ISSUER,
                INTERNAL_AUDIENCE
            ),
            Err(SignerError::InvalidPrivateKeyPem { .. })
        ));
    }

    /// The JWKS document must be byte-identical to what the Go signer serves
    /// on GET /jwks (captured in the golden fixture), trailing newline
    /// included.
    #[test]
    fn jwks_document_matches_golden_byte_for_byte() {
        let golden = golden_tokens();
        let internal = &golden["internal"];
        let seed = base64::engine::general_purpose::STANDARD
            .decode(internal["seed_b64"].as_str().unwrap())
            .unwrap();
        let signer = Signer::from_seed(&seed, INTERNAL_ISSUER, INTERNAL_AUDIENCE).unwrap();

        // Structural comparison against the fixture (MarshalIndent re-indented
        // the captured body, so byte comparison happens against the re-compacted
        // form; key order survives serde_json's order-preserving parse).
        let expected_value: serde_json::Value = internal["jwks"].clone();
        let served: serde_json::Value = serde_json::from_str(&signer.jwks_document()).unwrap();
        assert_eq!(served, expected_value);

        // Exact wire shape: single-line JSON, go-jose field order, trailing
        // newline from json.Encoder.Encode.
        let x = internal["jwks"]["keys"][0]["x"].as_str().unwrap();
        let y = internal["jwks"]["keys"][0]["y"].as_str().unwrap();
        assert_eq!(
            signer.jwks_document(),
            format!(
                "{{\"keys\":[{{\"use\":\"sig\",\"kty\":\"EC\",\"kid\":\"default\",\"crv\":\"P-256\",\"alg\":\"ES256\",\"x\":\"{x}\",\"y\":\"{y}\"}}]}}\n"
            )
        );
    }

    /// Self-consistency: a third party can verify our tokens using only the
    /// JWKS document (what the Kubernetes JWT authenticator does in Go).
    #[test]
    fn same_claims_produce_distinct_but_valid_tokens() {
        // Rotation must yield a new token even when nothing else changes (Go
        // signs with a random nonce; e2e "double rotation produces distinct
        // tokens"). Two mints at the SAME `iat` (identical claims) must differ
        // by signature yet both validate.
        let signer = test_signer();
        let a = signer.token_at("client:ns:name:uid", NOW as i64).unwrap();
        let b = signer.token_at("client:ns:name:uid", NOW as i64).unwrap();
        assert_ne!(a, b, "same-claims tokens must differ (randomized ECDSA)");
        // Identical header + claims, differing only in the signature segment.
        assert_eq!(
            a.rsplit_once('.').unwrap().0,
            b.rsplit_once('.').unwrap().0,
            "header+claims (signing input) must be identical"
        );
        signer.validate_at(&a, NOW as f64).unwrap();
        signer.validate_at(&b, NOW as f64).unwrap();
    }

    #[test]
    fn token_verifies_via_jwks_components() {
        let signer = test_signer();
        // Mint at the real clock (default 365d lifetime): this test verifies
        // through jsonwebtoken's default `Validation`, which checks `exp`
        // against the wall clock, so a fixed past `iat` would make the token
        // expire over calendar time.
        let token = signer.token("sub").unwrap();

        let jwks: serde_json::Value = serde_json::from_str(&signer.jwks_document()).unwrap();
        let key = &jwks["keys"][0];
        assert_eq!(key["kid"], "default");
        assert_eq!(key["use"], "sig");
        assert_eq!(key["alg"], "ES256");
        assert_eq!(key["kty"], "EC");
        assert_eq!(key["crv"], "P-256");

        let decoding = jsonwebtoken::DecodingKey::from_ec_components(
            key["x"].as_str().unwrap(),
            key["y"].as_str().unwrap(),
        )
        .unwrap();
        let mut validation = jsonwebtoken::Validation::new(jsonwebtoken::Algorithm::ES256);
        validation.set_audience(&[INTERNAL_AUDIENCE]);
        validation.set_issuer(&[INTERNAL_ISSUER]);
        jsonwebtoken::decode::<serde_json::Value>(&token, &decoding, &validation)
            .expect("token verifies through the JWKS document");
    }

    /// Derivation determinism: same seed, same key across constructions
    /// (token validity must survive controller restarts, spec 07 §13.1).
    #[test]
    fn seed_derivation_is_deterministic() {
        let a = test_signer();
        let b = test_signer();
        assert_eq!(a.public_key(), b.public_key());
        let token = a.token("sub").unwrap();
        b.validate(&token)
            .expect("second signer validates the first's token");
    }
}
