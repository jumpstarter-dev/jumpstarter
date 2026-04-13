//! Jumpstarter testing utilities for Rust.
//!
//! Provides the `#[jumpstarter_test]` proc macro and session setup helpers
//! for writing hardware test functions.
//!
//! # Example
//!
//! ```no_run
//! use jumpstarter_testing::jumpstarter_test;
//!
//! #[jumpstarter_test]
//! async fn test_power_on(device: MyDevBoardDevice<'_>) {
//!     device.power.on().await.unwrap();
//! }
//! ```

use std::sync::LazyLock;

pub use jumpstarter_client::ExporterSession;
pub use jumpstarter_testing_macros::jumpstarter_test;

/// Shared tokio runtime and exporter session for all `#[jumpstarter_test]` functions.
///
/// Initialized once on first access. All tests share the same runtime so that
/// tonic gRPC channels remain alive across test function boundaries.
///
/// **Important:** Tests must run with `--test-threads=1` to avoid concurrent
/// access to the exporter.
static SHARED: LazyLock<(tokio::runtime::Runtime, ExporterSession)> = LazyLock::new(|| {
    let rt = tokio::runtime::Runtime::new().expect("failed to create tokio runtime");
    let session = rt
        .block_on(ExporterSession::from_env())
        .expect("JUMPSTARTER_HOST must be set — run inside jmp shell");
    (rt, session)
});

/// Get the shared tokio runtime and exporter session.
///
/// Called by the `#[jumpstarter_test]` macro expansion. Not intended for
/// direct use — use the macro instead.
pub fn shared_runtime_and_session() -> (&'static tokio::runtime::Runtime, &'static ExporterSession)
{
    let (rt, session) = &*SHARED;
    (rt, session)
}
