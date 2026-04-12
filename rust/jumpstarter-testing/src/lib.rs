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
//!     device.power.on(()).await.unwrap();
//! }
//! ```

pub use jumpstarter_client::ExporterSession;
pub use jumpstarter_testing_macros::jumpstarter_test;
