//! Tracing bootstrap for the manager/router binaries.
//!
//! The Go binaries build a zap logger from the `zap-*` flag surface
//! (`ctrl.SetLogger(zap.New(zap.UseFlagOptions(&opts)))` in
//! `controller/cmd/main.go`). The Rust port logs through `tracing` instead:
//! output goes to stderr, filtering is `EnvFilter`-based with an `info`
//! default, `RUST_LOG` is honored as the operational override, and a
//! `zap-log-level` flag value (the only zap flag with a behavioral mapping)
//! supplies the default directive when `RUST_LOG` is unset.

use crate::flags::ZapLogLevel;
use tracing_subscriber::EnvFilter;

/// Map a parsed `zap-log-level` value onto a tracing filter directive.
///
/// zap levels: named `debug`/`info`/`error`/`panic`, plus numeric custom
/// debug levels of increasing verbosity (`-n` for flag value `n`). tracing
/// has no `panic` level (mapped to `error`) and models "more verbose than
/// debug" as `trace` (mapped from verbosity >= 2).
pub fn zap_level_directive(level: &ZapLogLevel) -> &'static str {
    match level {
        ZapLogLevel::Debug => "debug",
        ZapLogLevel::Info => "info",
        ZapLogLevel::Error => "error",
        ZapLogLevel::Panic => "error",
        ZapLogLevel::Verbosity(1) => "debug",
        ZapLogLevel::Verbosity(_) => "trace",
    }
}

/// Initialize the global tracing subscriber: stderr writer, `EnvFilter`
/// built from `RUST_LOG` when set (and valid), otherwise from the
/// `zap-log-level` flag value, otherwise `info`.
///
/// Idempotent: uses `try_init` and reports whether *this* call installed
/// the subscriber (`false` means one was already set — e.g. by a test
/// harness or an earlier call — which is not an error).
pub fn init_tracing(zap_log_level: Option<&ZapLogLevel>) -> bool {
    let default_directive = zap_log_level.map(zap_level_directive).unwrap_or("info");
    // try_from_default_env errors when RUST_LOG is unset or unparsable; in
    // both cases fall back to the flag-derived default.
    let filter =
        EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new(default_directive));
    tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_writer(std::io::stderr)
        .try_init()
        .is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn zap_level_mapping() {
        assert_eq!(zap_level_directive(&ZapLogLevel::Debug), "debug");
        assert_eq!(zap_level_directive(&ZapLogLevel::Info), "info");
        assert_eq!(zap_level_directive(&ZapLogLevel::Error), "error");
        // tracing has no panic level; zap panic maps to error.
        assert_eq!(zap_level_directive(&ZapLogLevel::Panic), "error");
        assert_eq!(zap_level_directive(&ZapLogLevel::Verbosity(1)), "debug");
        assert_eq!(zap_level_directive(&ZapLogLevel::Verbosity(2)), "trace");
        assert_eq!(zap_level_directive(&ZapLogLevel::Verbosity(127)), "trace");
    }

    #[test]
    fn init_is_idempotent() {
        // Whichever call wins the global-subscriber race, a second call in
        // the same process must be a no-op rather than a panic/error.
        let _ = init_tracing(Some(&ZapLogLevel::Debug));
        assert!(!init_tracing(None), "second init must not reinstall");
        assert!(!init_tracing(Some(&ZapLogLevel::Error)));
    }
}
