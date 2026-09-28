//! Private selection of the local IPC implementation for the target platform.
//!
//! Only the Windows backend exists today. The public `local` module exposes its
//! resource types under `cfg(windows)`. Future backends belong here, behind the
//! same domain API, with their ownership and permission guarantees documented
//! and tested on the new target. Callers must not import platform modules.

#[cfg(windows)]
mod windows;

#[cfg(windows)]
pub use windows::{PrivateDirectory, UnixListener, UnixStream};
