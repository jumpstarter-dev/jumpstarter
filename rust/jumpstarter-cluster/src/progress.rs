//! Progress reporting for cluster operations — the Rust analog of the Python
//! `OutputCallback` (`callbacks.py`): status messages + a `confirm` hook for
//! destructive operations. The CLI passes a printing impl; library tests pass
//! [`Silent`].

pub trait Progress: Send + Sync {
    fn progress(&self, _msg: &str) {}
    fn success(&self, _msg: &str) {}
    fn warning(&self, _msg: &str) {}
    fn error(&self, _msg: &str) {}
    /// Confirm a destructive operation. The default auto-confirms (matching the
    /// Python `SilentCallback`/`ForceClickCallback`).
    fn confirm(&self, _prompt: &str) -> bool {
        true
    }
}

/// Reports nothing and auto-confirms — for JSON/YAML output and tests.
pub struct Silent;
impl Progress for Silent {}
