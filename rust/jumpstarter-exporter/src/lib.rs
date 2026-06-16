//! Jumpstarter exporter runtime (spec doc 03; plan in
//! `rust/docs/02-exporter-core-plan.md`).
//!
//! Increment 1: a Rust exporter that registers with the controller, consumes the
//! `Status`/`Listen` streams, and serves one lease at a time by hosting the real
//! Python drivers in a subprocess (`ExporterConfig.serve_unix_async`) and bridging
//! the router to that session socket (the reverse of the Phase-A client transport
//! host). Hooks, the supervisor fork/restart loop, standalone TCP, and the full
//! lease-lifecycle FSM are subsequent increments.

pub mod driver_host;
pub mod exporter;

/// The exporter reuses the client's error taxonomy (RPC / transport / config) for
/// the shared controller-channel and router-bridge paths.
pub type Error = jumpstarter_client::ClientError;

pub use driver_host::DriverHost;
pub use exporter::{run, RunOptions};
