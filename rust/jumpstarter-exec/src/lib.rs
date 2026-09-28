//! Remote command execution for the sidecar pattern.
//!
//! The execution client and server currently require Unix. Protocol messages
//! and logging remain available on all platforms.
//!
//! # Platform availability
//!
//! The `client` and `server` modules and socket integration tests are compiled
//! under `cfg(unix)`. They use Unix sockets, permissions, and process signals.
//! On Windows, the protocol, logging, and `version` command are available;
//! `serve`, `exec`, and `shutdown` report an unsupported-platform error. There
//! is no native Windows execution backend for this sidecar bridge yet.

#[cfg(unix)]
pub mod client;
pub mod log;
pub mod protocol;
#[cfg(unix)]
pub mod server;
