//! Owned process primitives, independent of Python.
//!
//! Public facades define ownership and error contracts; private platform modules
//! implement the operating-system operations. Process containment can be
//! consumed directly by Rust runtimes or language bindings.
//!
//! Only the Windows backend is implemented today. The `process` module is
//! exported only under `cfg(windows)`; its module documentation describes the
//! available guarantees. Linux and macOS have no implementation
//! or fallback in this crate.
//!
//! Callers own process spawning, graceful shutdown, deadlines, and reaping.
//! This crate does not implement shells, IPC, or asynchronous task scheduling.

mod platform;

/// Ownership and cleanup of a child process tree.
#[cfg(windows)]
pub mod process;
