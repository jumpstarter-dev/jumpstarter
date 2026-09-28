//! Scoped output modes for the process's Windows console screen buffer.

use std::io;

use crossterm_winapi::{ConsoleMode, Handle};

// Console mode flags from wincon.h.
const ENABLE_PROCESSED_OUTPUT: u32 = 0x0001;
const ENABLE_VIRTUAL_TERMINAL_PROCESSING: u32 = 0x0004;

/// Enables VT output while preserving wrapping and every other existing flag.
///
/// The guard uses the process's standard-output handle as it was at creation
/// and never closes or replaces it. Console modes belong to the screen buffer,
/// so callers must serialize changes to that buffer and restore nested guards
/// in reverse order. This does not change encoding, input modes, or perform
/// output.
pub(crate) struct OutputMode {
    console: ConsoleMode,
    original: Option<u32>,
}

impl OutputMode {
    /// Captures standard output's mode and enables processed VT output.
    ///
    /// Redirected files and pipes are not consoles and return an error. If
    /// enabling fails, the original mode is restored before returning the error.
    pub(crate) fn stdout() -> io::Result<Self> {
        // A shared standard handle: crossterm_winapi does not close it on drop.
        let console = ConsoleMode::from(Handle::output_handle()?);
        let original = console.mode()?;

        // Arm restoration before changing the mode, including the error path.
        let guard = Self {
            console,
            original: Some(original),
        };
        guard
            .console
            .set_mode(original | ENABLE_PROCESSED_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING)?;
        Ok(guard)
    }

    /// Restores the captured mode. Successful restoration is idempotent.
    ///
    /// A failed restoration keeps the guard armed, allowing an explicit retry
    /// and another best-effort attempt during drop.
    pub(crate) fn restore(&mut self) -> io::Result<()> {
        if let Some(original) = self.original {
            self.console.set_mode(original)?;
            self.original = None;
        }
        Ok(())
    }
}

impl Drop for OutputMode {
    fn drop(&mut self) {
        let _ = self.restore();
    }
}
