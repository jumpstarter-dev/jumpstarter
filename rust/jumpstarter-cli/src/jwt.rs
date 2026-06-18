//! Local JWT claim decoding — no signature verification, no network
//! (`jumpstarter_cli_common/oidc.py`: `decode_jwt`, `decode_jwt_issuer`,
//! `get_token_remaining_seconds`). The CLI only needs to read claims to report
//! token status and discover the issuer.

use base64::Engine;
use chrono::Utc;
use serde_json::{Map, Value};

/// The decoded JWT payload claims.
pub struct Claims {
    raw: Map<String, Value>,
}

impl Claims {
    pub fn get_str(&self, key: &str) -> Option<&str> {
        self.raw.get(key).and_then(Value::as_str)
    }
    pub fn get_i64(&self, key: &str) -> Option<i64> {
        self.raw.get(key).and_then(Value::as_i64)
    }
}

/// Decode the claims (payload) of a compact JWS without verifying the signature.
pub fn decode_claims(token: &str) -> Result<Claims, String> {
    let payload = token
        .split('.')
        .nth(1)
        .ok_or_else(|| "invalid token: missing payload segment".to_string())?;
    // JWT uses base64url without padding; tolerate stray padding defensively.
    // Errors are framed like Python's `decode_jwt` ("Invalid JWT format: ...").
    let bytes = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(payload.trim_end_matches('='))
        .map_err(|e| format!("Invalid JWT format: {e}"))?;
    let value: Value =
        serde_json::from_slice(&bytes).map_err(|e| format!("Invalid JWT format: {e}"))?;
    match value {
        Value::Object(raw) => Ok(Claims { raw }),
        _ => Err("Invalid JWT format: payload is not a JSON object".to_string()),
    }
}

/// The `iss` claim, if present (`decode_jwt_issuer`).
pub fn issuer(token: &str) -> Result<Option<String>, String> {
    Ok(decode_claims(token)?.get_str("iss").map(String::from))
}

/// Seconds until `exp` (negative if already expired), or `None` if the token can't
/// be decoded or has no `exp` (`get_token_remaining_seconds`).
pub fn remaining_seconds(token: &str) -> Option<i64> {
    let exp = decode_claims(token).ok()?.get_i64("exp")?;
    Some(exp - Utc::now().timestamp())
}
