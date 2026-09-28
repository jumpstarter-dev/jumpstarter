//! Python ownership wrapper for the reusable Windows process-tree guard.

use std::os::windows::io::{AsRawHandle, RawHandle};

use jumpstarter_proc::process;
use pyo3::prelude::*;

use crate::py_io;

/// A child's process handle owned by Python, such as `subprocess.Popen._handle`
/// or `multiprocessing.Process.sentinel`.
struct PythonProcessHandle(isize);

impl AsRawHandle for PythonProcessHandle {
    fn as_raw_handle(&self) -> RawHandle {
        self.0 as RawHandle
    }
}

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
    fn new(py: Python<'_>, handle: isize) -> PyResult<Self> {
        py.detach(|| process::ChildProcessTree::new(&PythonProcessHandle(handle)))
            .map(|inner| Self { inner })
            .map_err(py_io)
    }

    /// Terminates remaining descendants; safe to call more than once.
    fn close(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| self.inner.close()).map_err(py_io)
    }
}
