//! Public local IPC API, independent of the selected platform backend.
//!
//! Callers use `jumpstarter_ipc::local::{PrivateDirectory, UnixListener,
//! UnixStream}`. The platform implementation remains private. These resource
//! types are currently available only under `cfg(windows)`; no Linux/macOS
//! backend or async-runtime integration is implemented by this crate.
//!
//! # Nonblocking stream contract
//!
//! Operations return `std::io::Result` and never wait for socket readiness:
//!
//! - `UnixListener::bind` creates a listener without removing an existing path.
//!   `try_accept` returns an independently owned stream, or `None` if pending.
//! - `UnixStream::connect` starts a connection. Call `finish_connect` before
//!   I/O; it returns `false` while pending and reports connection failures.
//! - `try_recv` fills a caller-owned, nonempty buffer. `None` means pending,
//!   `Some(0)` means EOF, and `Some(n)` identifies the initialized prefix.
//! - `try_send` returns a possibly partial byte count, or `None` if pending.
//!   Callers retain unsent bytes and retry when ready.
//! - `shutdown_write` half-closes writes while allowing remaining reads.
//!
//! Callers supply buffers, readiness scheduling, deadlines, and cancellation.
//! No Python interpreter or async runtime is needed to use these operations.
//!
//! # Ownership and cleanup
//!
//! Socket operations serialize against `close`. Close is idempotent, and
//! ordinary Rust drop closes handles. Closing a listener neither closes its
//! accepted streams nor removes its filesystem pathname.
//!
//! `PrivateDirectory` owns a directory and one reserved socket pathname. Close
//! all streams and listeners before closing the directory. Directory cleanup
//! removes only that socket file and the empty directory; unrelated children
//! cause an error. Explicit close reports cleanup errors and retains ownership
//! for a retry. Drop attempts cleanup without panicking.

#[cfg(windows)]
pub use crate::platform::{PrivateDirectory, UnixListener, UnixStream};
