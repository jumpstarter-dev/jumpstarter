//! Owned process and terminal primitives, independent of Python.
//!
//! Public facades define ownership and error contracts; private platform modules
//! implement the operating-system operations. Process containment and scoped
//! terminal modes can be consumed directly by Rust runtimes or language bindings.
//!
//! Only the Windows backend is implemented today. The `process` and `console`
//! modules are exported only under `cfg(windows)`; their module documentation
//! describes the available guarantees. Linux and macOS have no implementation
//! or fallback in this crate.
//!
//! Callers own process spawning, graceful shutdown, deadlines, and reaping.
//! This crate does not implement shells, IPC, or asynchronous task scheduling.

mod platform;

/// Scoped output modes for the current process's terminal.
#[cfg(windows)]
pub mod console;

/// Ownership and cleanup of a child process tree.
#[cfg(windows)]
pub mod process;
