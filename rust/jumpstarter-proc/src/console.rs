//! Scoped terminal output-mode changes for the current process.
//!
//! This module is currently available only on Windows. [`crate::console::OutputMode::stdout`]
//! enables processed VT output while preserving wrapping and every other existing
//! console flag. The guard uses the process's standard-output handle and never
//! closes or replaces it. Redirected files and pipes are not console screen
//! buffers and return an [`std::io::Error`].
//!
//! [`crate::console::OutputMode::restore`] restores the captured mode. Successful restoration is
//! idempotent; failed restoration keeps the guard armed for retry. Drop makes a
//! best-effort restoration attempt. Console modes belong to a shared screen
//! buffer, so callers must serialize changes and restore nested guards in reverse
//! order. The guard does not perform I/O or change encoding or input modes.
//!
//! There is no Linux or macOS backend yet.

use std::io;

use crate::platform;

/// Enables processed VT output while preserving existing terminal flags.
///
/// The Windows implementation uses the standard-output handle captured at
/// creation and never closes or replaces it. Console modes belong to the
/// screen buffer: serialize changes and restore nested guards in reverse
/// order. This guard does not perform I/O or change encoding.
pub struct OutputMode {
    inner: platform::console::OutputMode,
}

impl OutputMode {
    /// Captures standard output's mode and enables processed VT output.
    ///
    /// Redirected files and pipes are not consoles and return an error. If
    /// enabling fails, the original mode is restored before returning the error.
    pub fn stdout() -> io::Result<Self> {
        platform::console::OutputMode::stdout().map(|inner| Self { inner })
    }

    /// Restores the captured mode. Successful restoration is idempotent.
    ///
    /// A failed restoration keeps the guard armed for a retry. Dropping the
    /// guard also attempts restoration, but cannot report an error.
    pub fn restore(&mut self) -> io::Result<()> {
        self.inner.restore()
    }
}
