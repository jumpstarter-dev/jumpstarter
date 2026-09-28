//! Python context management for the reusable Rust console-mode guard.

use std::io;
use std::sync::Mutex;

use jumpstarter_proc::console;
use pyo3::prelude::*;

use crate::py_io;

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<OutputMode>()?;
    Ok(())
}

/// Temporarily enables VT output on Windows standard output.
#[pyclass(module = "jumpstarter_core.console")]
pub struct OutputMode {
    inner: Mutex<Option<console::OutputMode>>,
}

impl OutputMode {
    fn enable(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| {
            let mut guard = self
                .inner
                .lock()
                .map_err(|_| io::Error::other("console mode lock poisoned"))?;
            if guard.is_some() {
                return Err(io::Error::new(
                    io::ErrorKind::AlreadyExists,
                    "console output mode is already active",
                ));
            }
            *guard = Some(console::OutputMode::stdout()?);
            Ok(())
        })
        .map_err(py_io)
    }
}

#[pymethods]
impl OutputMode {
    #[new]
    fn new() -> Self {
        Self {
            inner: Mutex::new(None),
        }
    }

    fn __enter__(slf: PyRef<'_, Self>) -> PyResult<PyRef<'_, Self>> {
        slf.enable(slf.py())?;
        Ok(slf)
    }

    fn __exit__(
        &self,
        py: Python<'_>,
        _exc_type: &Bound<'_, PyAny>,
        _exc_value: &Bound<'_, PyAny>,
        _traceback: &Bound<'_, PyAny>,
    ) -> PyResult<()> {
        self.close(py)
    }

    /// Restores the saved mode and releases the guard.
    fn close(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| {
            let mut guard = self
                .inner
                .lock()
                .map_err(|_| io::Error::other("console mode lock poisoned"))?;
            if let Some(active) = guard.as_mut() {
                active.restore()?;
            }
            guard.take();
            Ok(())
        })
        .map_err(py_io)
    }
}
