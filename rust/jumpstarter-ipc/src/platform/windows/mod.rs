//! Windows socket and private-directory implementation details.
//!
//! This backend supplies the public `local` resource types using Windows
//! AF_UNIX sockets and filesystem paths. No Linux/macOS implementation or
//! fallback transport is selected here.
//!
//! # Socket implementation
//!
//! `socket2` owns non-inheritable socket handles, nonblocking connect/accept,
//! byte I/O, shutdown, and handle cleanup. A zero-timeout Winsock `select` call
//! checks connection completion; the socket's error status determines failure.
//! Each operation is serialized against close. None waits for readiness, so
//! consumers provide scheduling and cancellation. See `local.rs` for the
//! implementation and the public `crate::local` module for its contract.
//!
//! # Directory and pathname boundary
//!
//! Directory creation atomically applies a protected, inheritable DACL granting
//! full control only to the current user and SYSTEM, then verifies that DACL.
//! It does not rely on permissions inherited from the temporary-directory base.
//! Cleanup removes only the reserved socket file and the empty owned directory.
//! See `private_directory.rs` for the security and cleanup implementation.
//!
//! Socket paths must be absolute, contain no NUL, encode as UTF-8, and fit in
//! 107 UTF-8 bytes. Creation checks the final generated path before creating a
//! directory. If an overlong base has a usable Windows short-path alias, that
//! alias is used; otherwise creation fails and the caller must provide a
//! shorter existing base. There is no TCP fallback.
//!
//! # Standalone Rust verification
//!
//! On Windows, run from the Rust workspace directory:
//!
//! ```text
//! cargo run -p jumpstarter-ipc --example local_roundtrip
//! ```
//!
//! The example checks binary I/O, pending reads, partial-write handling,
//! half-close, independent listener/stream ownership, explicit close, and drop
//! cleanup without Python or an async runtime.

mod local;
mod private_directory;

pub use local::{UnixListener, UnixStream};
pub use private_directory::PrivateDirectory;

use std::io;
use std::sync::{Mutex, MutexGuard};

fn lock<T>(mutex: &Mutex<T>) -> io::Result<MutexGuard<'_, T>> {
    mutex
        .lock()
        .map_err(|_| io::Error::other("local IPC lock poisoned"))
}
