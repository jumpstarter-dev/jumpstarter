//! Python ownership wrapper for the reusable Windows process-tree guard.

use jumpstarter_proc::process;
use pyo3::prelude::*;

use crate::py_io;

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<ChildProcessTree>()?;
    Ok(())
}

/// Keeps an assigned child and its later descendants in a kill-on-close job.
#[pyclass(module = "jumpstarter_core.process")]
pub struct ChildProcessTree {
    inner: process::ChildProcessTree,
}

#[pymethods]
impl ChildProcessTree {
    #[new]
    fn new(py: Python<'_>, pid: u32) -> PyResult<Self> {
        py.detach(|| process::ChildProcessTree::new(pid))
            .map(|inner| Self { inner })
            .map_err(py_io)
    }

    /// Terminates remaining descendants; safe to call more than once.
    fn close(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| self.inner.close()).map_err(py_io)
    }
}
