//! Owned OS-local IPC primitives, independent of Python and asynchronous runtimes.
//!
//! The public [`local`] module defines the resource boundary and documents
//! target availability. Its nonblocking operations use [`std::io::Result`];
//! callers own readiness scheduling, deadlines, and cancellation. Python
//! bindings and async adapters are separate consumers of these primitives.
//! This crate does not implement routing, leases, authentication protocols,
//! process supervision, terminal modes, or the Jumpstarter wire protocol.

mod platform;

/// OS-local stream and private-directory primitives.
///
pub mod local;
