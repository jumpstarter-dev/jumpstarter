//! Thin Python bindings for reusable jumpstarter-ipc and jumpstarter-proc APIs.
//!
//! This crate builds the private `jumpstarter_core._core` extension. Scoped
//! Python modules expose the domain APIs; ownership and OS operations remain
//! in the reusable crates. The binding converts arguments and errors and
//! releases the interpreter during native operations.
//!
//! # Platform availability
//!
//! The local IPC, process, and console classes are currently registered only
//! under `cfg(windows)`. On other targets the extension builds without those
//! classes; a successful build does not imply support for the scoped APIs.
//! Python module documentation carries their usage and backend requirements.

use pyo3::prelude::*;

#[cfg(windows)]
mod console;
#[cfg(windows)]
mod local;
#[cfg(windows)]
mod process;

#[cfg(windows)]
pub(crate) fn py_io(error: std::io::Error) -> PyErr {
    use pyo3::exceptions::PyOSError;

    if let Some(code) = error.raw_os_error() {
        // On Windows the fourth argument sets winerror and the mapped errno.
        PyOSError::new_err((0, error.to_string(), None::<String>, code))
    } else {
        PyOSError::new_err(error.to_string())
    }
}

#[pymodule]
fn _core(module: &Bound<'_, PyModule>) -> PyResult<()> {
    #[cfg(windows)]
    {
        local::register(module)?;
        console::register(module)?;
        process::register(module)?;
    }
    #[cfg(not(windows))]
    {
        let _ = module;
    }
    Ok(())
}
