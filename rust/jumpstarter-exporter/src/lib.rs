//! Jumpstarter exporter runtime (spec doc 03; native-migration design in
//! `rust/docs/03-native-exporter-migration.md`).
//!
//! A Rust exporter that registers with the controller, consumes the `Status`/`Listen`
//! streams, and serves one lease at a time. It **serves the client/hook-facing
//! `ExporterService` + `RouterService` itself** ([`session`] + [`tunnel`]) on its own
//! main + hook sockets, terminating each client tunnel into that server, and hosts the
//! real Python drivers in a slim per-lease [`driver_host::SlimHost`] subprocess that
//! it proxies driver calls into by UUID. The host is [pre-warmed](exporter) so a lease
//! doesn't pay the spawn cost.
//!
//! Each lease runs through the [`fsm`] lease-lifecycle state machine, executing the
//! `beforeLease`/`afterLease` [`hooks`] against the Rust hook socket and reporting the
//! status sequence both to the controller and the `GetStatus` snapshot via [`control`].
//!
//! Still deferred: the supervisor fork/restart loop + rapid-failure breaker, the
//! `_retry_stream` contract (5×1.0 s), and standalone TCP.

pub mod control;
pub mod driver_host;
pub mod exporter;
pub mod fsm;
pub mod hooks;
pub mod session;
pub mod tunnel;

/// The exporter reuses the client's error taxonomy (RPC / transport / config) for
/// the shared controller-channel and router-bridge paths.
pub type Error = jumpstarter_client::ClientError;

pub use driver_host::SlimHost;
pub use exporter::{run, RunOptions};
