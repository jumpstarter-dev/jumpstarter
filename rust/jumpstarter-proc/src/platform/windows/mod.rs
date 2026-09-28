//! Windows process containment and terminal-mode implementations.
//!
//! This is the only implemented backend. Public operations are exposed through
//! `crate::process` and `crate::console`; native handles stay private here.
//!
//! - [`process`] assigns a gated child to a private Job Object configured with
//!   `KILL_ON_JOB_CLOSE`. Closing the last job handle, including when the owning
//!   process dies, terminates remaining members. Children created before
//!   assignment are not captured. Assignment failures are reported, and callers
//!   retain graceful shutdown, deadline enforcement, and reaping.
//! - [`console`] enables processed VT output on standard output without
//!   changing wrapping or other flags, and restores the captured mode
//!   explicitly or on drop. It never closes the standard-output handle. Mode
//!   changes affect the shared screen buffer, so callers serialize them and
//!   restore nested guards in reverse order. Redirected output fails.
//!
//! Job Objects come from the `win32job` crate and console modes from
//! `crossterm_winapi` (the crossterm project's Windows console wrapper), so this
//! backend has no FFI of its own; no Python interpreter or bindings participate
//! in resource ownership or cleanup.

pub(crate) mod console;
pub(crate) mod process;
