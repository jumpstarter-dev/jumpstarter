//! Jumpstarter exporter runtime (spec doc 03; plan in
//! `rust/docs/02-exporter-core-plan.md`).
//!
//! A Rust exporter that registers with the controller, consumes the
//! `Status`/`Listen` streams, and serves one lease at a time by hosting the real
//! Python drivers in a subprocess (`session_host.py`) and bridging the router to
//! that session socket (the reverse of the Phase-A client transport host).
//!
//! Each lease runs through the [`fsm`] lease-lifecycle state machine, executing the
//! `beforeLease`/`afterLease` [`hooks`] against the session's isolated hook socket
//! and reporting the resulting status sequence to the controller via [`control`].
//!
//! Still deferred: the supervisor fork/restart loop + rapid-failure breaker, the
//! `_retry_stream` contract (5×1.0 s), standalone TCP, and per-lease driver
//! re-instantiation.

pub mod control;
pub mod driver_host;
pub mod exporter;
pub mod fsm;
pub mod hooks;
pub mod session;

/// The exporter reuses the client's error taxonomy (RPC / transport / config) for
/// the shared controller-channel and router-bridge paths.
pub type Error = jumpstarter_client::ClientError;

pub use driver_host::{DriverHost, SlimHost};
pub use exporter::{run, RunOptions};
