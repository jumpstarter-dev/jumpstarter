//! Ownership of a child process and its descendants.
//!
//! This module is currently available only on Windows. [`crate::process::ChildProcessTree`]
//! assigns an already-spawned child to a private, non-inheritable Job Object with
//! `KILL_ON_JOB_CLOSE`. Explicit close, Rust drop, or abrupt termination of the
//! guard's owning process closes the job handle and terminates remaining members.
//! Job handles are neither inherited nor exposed.
//!
//! Keep the child behind a startup handshake until assignment succeeds; earlier
//! descendants are not captured. The caller retains spawning, the startup gate,
//! graceful shutdown, deadlines, assignment-failure cleanup, and reaping. Closing
//! the guard is idempotent but does not wait for termination. Zero/current-process
//! IDs and incompatible existing job policies return errors rather than silently
//! weakening containment.
//!
//! There is no Linux or macOS backend yet. A future backend must explicitly
//! establish its containment guarantees; process groups alone must not be treated
//! as equivalent to the Windows owner-death cleanup contract.

use std::io;

use crate::platform;

/// Terminates the assigned child and its later descendants when closed or dropped.
///
/// On Windows, a private, non-inheritable Job Object also enforces this lifetime
/// when the owning process exits without running Rust destructors. The guard
/// does not wait for processes to finish or implement graceful shutdown.
pub struct ChildProcessTree {
    inner: platform::process::ChildProcessTree,
}

impl ChildProcessTree {
    /// Assigns an already-spawned child to the guard's process containment scope.
    ///
    /// Keep the child behind a startup handshake until this succeeds: processes
    /// started before assignment are not captured. The caller owns the child's
    /// process handle and must terminate the gated child if assignment fails.
    /// Incompatible containment policies return an error rather than leaving
    /// the child unmanaged. Zero and the current process ID are rejected.
    pub fn new(pid: u32) -> io::Result<Self> {
        platform::process::ChildProcessTree::new(pid).map(|inner| Self { inner })
    }

    /// Terminates remaining members. Safe to call more than once.
    ///
    /// The caller must still wait for and reap its child process.
    pub fn close(&self) -> io::Result<()> {
        self.inner.close()
    }
}
