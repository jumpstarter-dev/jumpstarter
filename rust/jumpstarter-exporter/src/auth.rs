//! Passphrase authentication for the standalone (`--tls-grpc-listener`) gRPC
//! server (`exporter/auth.py:PassphraseInterceptor`). A tonic request interceptor
//! that rejects RPCs whose metadata is missing or carries the wrong passphrase.

use tonic::{Request, Status};

/// Metadata key carrying the client passphrase (`auth.py:PASSPHRASE_METADATA_KEY`).
pub const PASSPHRASE_METADATA_KEY: &str = "x-jumpstarter-passphrase";

/// Build a per-request interceptor enforcing `passphrase`. `None` requires no
/// passphrase (every request passes). A missing or mismatched passphrase aborts the
/// RPC with `UNAUTHENTICATED "invalid or missing passphrase"`, matching Python.
pub fn passphrase_interceptor(
    passphrase: Option<String>,
) -> impl FnMut(Request<()>) -> Result<Request<()>, Status> + Clone {
    move |req: Request<()>| match &passphrase {
        None => Ok(req),
        Some(expected) => {
            let provided = req
                .metadata()
                .get(PASSPHRASE_METADATA_KEY)
                .map(|v| v.as_bytes());
            match provided {
                Some(p) if constant_time_eq(p, expected.as_bytes()) => Ok(req),
                _ => Err(Status::unauthenticated("invalid or missing passphrase")),
            }
        }
    }
}

/// Length-checked constant-time comparison (`hmac.compare_digest`).
fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.iter().zip(b) {
        diff |= x ^ y;
    }
    diff == 0
}
