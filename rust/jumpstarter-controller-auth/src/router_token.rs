//! Controller-side router stream-token **minting**, port of the HS256 issue in
//! `ControllerService.Dial` (`controller/internal/service/controller_service.go:864-879`).
//!
//! This is the mint end of the router-token contract; the *validation* end
//! lives in [`jumpstarter_router_service::auth`] (a line-by-line port of
//! `router_service.go:52-78`). The two are cross-checked in the tests below so
//! a claim-shape drift on either side is caught at `cargo test`.
//!
//! What Go does in `Dial` (`controller_service.go:864-879`):
//!
//! ```text
//! stream := k8suuid.NewUUID()                      // fresh UUIDv4 string
//! token, _ := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.RegisteredClaims{
//!     Issuer:    "https://jumpstarter.dev/stream",
//!     Subject:   string(stream),                   // the rendezvous key
//!     Audience:  []string{"https://jumpstarter.dev/router"},
//!     ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Minute * 30)),
//!     NotBefore: jwt.NewNumericDate(time.Now()),
//!     IssuedAt:  jwt.NewNumericDate(time.Now()),
//!     ID:        string(k8suuid.NewUUID()),         // fresh UUIDv4 jti
//! }).SignedString([]byte(os.Getenv("ROUTER_KEY")))
//! ```
//!
//! Faithfulness notes:
//!
//! - `k8s.io/apimachinery/pkg/util/uuid.NewUUID()` is `google/uuid.New()`, a
//!   **v4 (random)** UUID rendered as its canonical hyphenated string; the
//!   `sub` (stream/rendezvous id) and `jti` are two independent fresh v4s.
//! - golang-jwt v5 marshals `jwt.RegisteredClaims` in struct-field order with
//!   `omitempty`, so the wire order is `iss, sub, aud, exp, nbf, iat, jti` and
//!   the single-element `aud` stays a JSON **array**
//!   (`MarshalSingleStringAsArray` defaults to true). Pinned byte-for-byte in
//!   [`tests::claim_set_pins_go_shape`].
//! - `jwt.NewNumericDate(t)` truncates to whole seconds at the default
//!   `TimePrecision = time.Second`, so `exp`/`nbf`/`iat` are integer seconds.
//!   Go evaluates `time.Now()` three times a few nanoseconds apart, but within
//!   the same wall-clock second they collapse to one value; this mint takes a
//!   single `now` and uses it for all three (matching the observable output).
//! - The key is the **raw bytes** of `ROUTER_KEY` (`[]byte(os.Getenv(...))`),
//!   read by the caller (phase-5 `ControllerService`) and passed in; the
//!   router validator reads the same env per attempt
//!   ([`jumpstarter_router_service::auth::KeySource::Env`]).

use jsonwebtoken::{Algorithm, EncodingKey, Header};
use serde::Serialize;
use std::time::{SystemTime, UNIX_EPOCH};
use thiserror::Error;
use uuid::Uuid;

/// `iss` minted on router stream tokens (`controller_service.go:867`). Matches
/// [`jumpstarter_router_service::auth::STREAM_ISSUER`] (cross-checked in tests).
pub const STREAM_ISSUER: &str = "https://jumpstarter.dev/stream";
/// `aud` minted on router stream tokens (`controller_service.go:869`). Matches
/// [`jumpstarter_router_service::auth::ROUTER_AUDIENCE`].
pub const ROUTER_AUDIENCE: &str = "https://jumpstarter.dev/router";
/// Router token lifetime: `time.Minute * 30` (`controller_service.go:870`).
pub const ROUTER_TOKEN_LIFETIME_SECS: i64 = 30 * 60;

/// Failure minting a router token. Signing an HS256 token over an in-memory
/// key effectively cannot fail; the variant exists so callers can map it to
/// the Go `Internal "unable to sign token"` status (`controller_service.go:876-878`).
#[derive(Debug, Error)]
pub enum RouterTokenError {
    /// jsonwebtoken rejected the signing operation.
    #[error("failed to sign router token: {0}")]
    Jwt(#[from] jsonwebtoken::errors::Error),
}

/// The registered claims minted by `Dial`, in golang-jwt `RegisteredClaims`
/// struct-field order (`iss, sub, aud, exp, nbf, iat, jti`).
#[derive(Serialize)]
struct RouterClaims<'a> {
    iss: &'a str,
    sub: &'a str,
    aud: [&'a str; 1],
    exp: i64,
    nbf: i64,
    iat: i64,
    jti: &'a str,
}

/// Mints a router stream token signed with `router_key` (raw `ROUTER_KEY`
/// bytes) at issue time `now_unix_secs` (seconds since the Unix epoch — what
/// golang-jwt's `NumericDate` truncates `time.Now()` to). Returns the signed
/// compact JWT and the fresh **stream id** (`sub`, the router rendezvous key);
/// the `jti` is a second fresh UUID that Go discards after signing, so it is
/// not returned.
pub fn mint_router_token(
    router_key: &[u8],
    now_unix_secs: i64,
) -> Result<(String, String), RouterTokenError> {
    let stream_id = Uuid::new_v4().to_string();
    let jti = Uuid::new_v4().to_string();
    let token = encode_router_token(router_key, now_unix_secs, &stream_id, &jti)?;
    Ok((token, stream_id))
}

/// [`mint_router_token`] at the current system time (the production call).
pub fn mint_router_token_now(router_key: &[u8]) -> Result<(String, String), RouterTokenError> {
    mint_router_token(router_key, unix_now_secs())
}

/// The raw HMAC key bytes from env `ROUTER_KEY`, unset resolving to the empty
/// key exactly like Go's `[]byte(os.Getenv("ROUTER_KEY"))`
/// (`controller_service.go:874`). Mirrors
/// [`jumpstarter_router_service::auth::KeySource::Env`] so mint and validate
/// resolve the key identically.
pub fn router_key_from_env() -> Vec<u8> {
    std::env::var(jumpstarter_controller_config::env::ROUTER_KEY)
        .unwrap_or_default()
        .into_bytes()
}

/// The signing core, factored out with explicit `stream_id`/`jti` so the exact
/// claim byte order can be pinned in tests (the public entry points generate
/// fresh UUIDs).
fn encode_router_token(
    router_key: &[u8],
    now_unix_secs: i64,
    stream_id: &str,
    jti: &str,
) -> Result<String, RouterTokenError> {
    let claims = RouterClaims {
        iss: STREAM_ISSUER,
        sub: stream_id,
        aud: [ROUTER_AUDIENCE],
        exp: now_unix_secs + ROUTER_TOKEN_LIFETIME_SECS,
        nbf: now_unix_secs,
        iat: now_unix_secs,
        jti,
    };
    Ok(jsonwebtoken::encode(
        &Header::new(Algorithm::HS256),
        &claims,
        &EncodingKey::from_secret(router_key),
    )?)
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
    use base64::Engine as _;

    const KEY: &[u8] = b"golden-router-key";
    const NOW: i64 = 1_750_000_000;

    fn payload_json(token: &str) -> String {
        let payload = token.split('.').nth(1).expect("three-segment JWT");
        let bytes = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .decode(payload)
            .expect("base64url payload");
        String::from_utf8(bytes).expect("utf8 payload")
    }

    fn header_json(token: &str) -> String {
        let header = token.split('.').next().expect("header segment");
        let bytes = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .decode(header)
            .expect("base64url header");
        String::from_utf8(bytes).expect("utf8 header")
    }

    /// Exact claim set + JSON key order of `Dial`
    /// (`controller_service.go:866-874`): `iss, sub, aud[array], exp, nbf, iat,
    /// jti`, integer numeric dates, `exp = now + 30m`, `nbf == iat == now`.
    #[test]
    fn claim_set_pins_go_shape() {
        let stream = "f81d4fae-7dec-11d0-a765-00a0c91e6bf6";
        let jti = "0c7f9a52-1a3b-4d5e-8f90-abcdef012345";
        let token = encode_router_token(KEY, NOW, stream, jti).unwrap();

        let expected = format!(
            r#"{{"iss":"{STREAM_ISSUER}","sub":"{stream}","aud":["{ROUTER_AUDIENCE}"],"exp":{},"nbf":{NOW},"iat":{NOW},"jti":"{jti}"}}"#,
            NOW + ROUTER_TOKEN_LIFETIME_SECS,
        );
        assert_eq!(payload_json(&token), expected);
        // Header fields match golang-jwt (alg HS256, typ JWT). jsonwebtoken
        // serializes them as {"typ","alg"} where golang-jwt emits
        // {"alg","typ"}; a JWT header is self-describing and re-parsed by every
        // validator (the signature covers whatever bytes are present), so this
        // byte-order difference is not a compatibility concern — the claims,
        // which drive rendezvous and authz, are byte-identical to Go.
        let header: serde_json::Value = serde_json::from_str(&header_json(&token)).unwrap();
        assert_eq!(header["alg"], "HS256");
        assert_eq!(header["typ"], "JWT");
    }

    /// The returned stream id is the token's `sub`; both stream id and jti are
    /// fresh, canonical v4 UUIDs and distinct from each other.
    #[test]
    fn mint_returns_stream_id_matching_sub() {
        let (token, stream_id) = mint_router_token(KEY, NOW).unwrap();
        let claims: serde_json::Value = serde_json::from_str(&payload_json(&token)).unwrap();

        assert_eq!(claims["sub"].as_str().unwrap(), stream_id);
        let sub = Uuid::parse_str(stream_id.as_str()).expect("sub is a UUID");
        let jti = Uuid::parse_str(claims["jti"].as_str().unwrap()).expect("jti is a UUID");
        assert_eq!(sub.get_version(), Some(uuid::Version::Random));
        assert_eq!(jti.get_version(), Some(uuid::Version::Random));
        assert_ne!(sub, jti, "stream id and jti are independent UUIDs");
    }

    #[test]
    fn lifetime_is_thirty_minutes_from_now() {
        let (token, _) = mint_router_token(KEY, NOW).unwrap();
        let claims: serde_json::Value = serde_json::from_str(&payload_json(&token)).unwrap();
        let exp = claims["exp"].as_i64().unwrap();
        let nbf = claims["nbf"].as_i64().unwrap();
        let iat = claims["iat"].as_i64().unwrap();
        assert_eq!(iat, NOW);
        assert_eq!(nbf, NOW);
        assert_eq!(exp - iat, ROUTER_TOKEN_LIFETIME_SECS);
    }

    /// Every mint uses a fresh rendezvous key and jti, so both the stream id
    /// and the full token differ across calls (no reuse).
    #[test]
    fn fresh_ids_each_call() {
        let (token_a, stream_a) = mint_router_token(KEY, NOW).unwrap();
        let (token_b, stream_b) = mint_router_token(KEY, NOW).unwrap();
        assert_ne!(stream_a, stream_b);
        assert_ne!(token_a, token_b);
    }

    // -- Cross-validation against the real router-service validator ----------

    /// The mint and validate constants are two ends of one contract; they must
    /// be literally equal.
    #[test]
    fn constants_match_router_service() {
        use jumpstarter_router_service::auth;
        assert_eq!(STREAM_ISSUER, auth::STREAM_ISSUER);
        assert_eq!(ROUTER_AUDIENCE, auth::ROUTER_AUDIENCE);
    }

    /// A controller-minted token validates in the router service and yields the
    /// same `sub` we handed back as the stream id (the rendezvous round trip).
    #[test]
    fn minted_token_validates_in_router_service() {
        use jumpstarter_router_service::auth;

        let (token, stream_id) = mint_router_token(KEY, NOW).unwrap();
        // Validate a moment after issue, well inside the 30m window.
        let sub = auth::validate_token(&token, KEY, (NOW + 5) as f64)
            .expect("controller-minted token validates in the router");
        assert_eq!(sub, stream_id);

        // Wrong key is rejected by the router validator (HS256 signature).
        assert!(auth::validate_token(&token, b"other-key", (NOW + 5) as f64).is_err());
        // At/after exp the router treats it as expired (now.Before(exp)).
        assert!(matches!(
            auth::validate_token(&token, KEY, (NOW + ROUTER_TOKEN_LIFETIME_SECS) as f64),
            Err(auth::TokenError::Expired)
        ));
    }

    /// Go-minted golden router tokens validate through the router service's
    /// validator with the shared golden key (Go -> Rust cross-verification of
    /// the whole claim set, exercised end-to-end via the real validator rather
    /// than a bare jsonwebtoken decode).
    #[test]
    fn golden_go_router_tokens_validate_in_router_service() {
        use jumpstarter_router_service::auth;

        let golden: serde_json::Value =
            serde_json::from_str(include_str!("../tests/golden/tokens.json")).unwrap();
        let router = &golden["router"];
        let key = router["key"].as_str().unwrap().as_bytes();
        let tokens = router["tokens"].as_array().unwrap();
        assert_eq!(tokens.len(), 2);

        for entry in tokens {
            let token = entry["token"].as_str().unwrap();
            let want_sub = entry["claims"]["sub"].as_str().unwrap();
            // Golden tokens use a +100y exp; validate at their own iat.
            let iat = entry["claims"]["iat"].as_f64().unwrap();
            let sub = auth::validate_token(token, key, iat)
                .expect("Go-minted router token validates in the router service");
            assert_eq!(sub, want_sub);
        }
    }
}
