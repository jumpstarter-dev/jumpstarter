//! Python conversion and interpreter release only. IPC behavior is implemented
//! by the reusable, Python-free jumpstarter-ipc crate.

use std::path::Path;

use jumpstarter_ipc::local as ipc;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

use crate::py_io;

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<PrivateDirectory>()?;
    module.add_class::<UnixListener>()?;
    module.add_class::<UnixStream>()?;
    Ok(())
}

#[pyclass(module = "jumpstarter_core.local")]
pub struct PrivateDirectory {
    inner: ipc::PrivateDirectory,
}

#[pymethods]
impl PrivateDirectory {
    #[staticmethod]
    #[pyo3(signature = (base=None))]
    fn create(py: Python<'_>, base: Option<String>) -> PyResult<Self> {
        py.detach(|| ipc::PrivateDirectory::create(base.as_deref().map(Path::new)))
            .map(|inner| Self { inner })
            .map_err(py_io)
    }

    #[getter]
    fn socket_path(&self) -> &str {
        // The IPC crate validates UTF-8 before creating a socket directory.
        self.inner
            .socket_path()
            .to_str()
            .expect("validated UTF-8 socket path")
    }

    fn close(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| self.inner.close()).map_err(py_io)
    }
}

#[pyclass(module = "jumpstarter_core.local")]
pub struct UnixListener {
    inner: ipc::UnixListener,
}

#[pymethods]
impl UnixListener {
    #[staticmethod]
    #[pyo3(signature = (path, backlog=128))]
    fn bind(py: Python<'_>, path: String, backlog: i32) -> PyResult<Self> {
        if backlog < 1 {
            return Err(PyValueError::new_err("backlog must be at least 1"));
        }
        py.detach(|| ipc::UnixListener::bind_with_backlog(path, backlog))
            .map(|inner| Self { inner })
            .map_err(py_io)
    }

    /// The socket handle, only for readiness waits such as `select`.
    fn fileno(&self) -> PyResult<u64> {
        self.inner.raw_socket().map_err(py_io)
    }

    fn try_accept(&self, py: Python<'_>) -> PyResult<Option<UnixStream>> {
        py.detach(|| self.inner.try_accept())
            .map(|stream| stream.map(|inner| UnixStream { inner }))
            .map_err(py_io)
    }

    fn close(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| self.inner.close()).map_err(py_io)
    }
}

#[pyclass(module = "jumpstarter_core.local")]
pub struct UnixStream {
    inner: ipc::UnixStream,
}

#[pymethods]
impl UnixStream {
    #[staticmethod]
    fn connect(py: Python<'_>, path: String) -> PyResult<Self> {
        py.detach(|| ipc::UnixStream::connect(path))
            .map(|inner| Self { inner })
            .map_err(py_io)
    }

    /// The socket handle, only for readiness waits such as `select`.
    fn fileno(&self) -> PyResult<u64> {
        self.inner.raw_socket().map_err(py_io)
    }

    fn finish_connect(&self, py: Python<'_>) -> PyResult<bool> {
        py.detach(|| self.inner.finish_connect()).map_err(py_io)
    }

    fn try_recv<'py>(
        &self,
        py: Python<'py>,
        max_bytes: usize,
    ) -> PyResult<Option<Bound<'py, PyBytes>>> {
        if max_bytes == 0 {
            return Err(PyValueError::new_err("max_bytes must be at least 1"));
        }
        let data = py
            .detach(|| {
                // Preserve the Python receive contract and bound allocations. Rust
                // consumers provide their own buffer directly to the IPC crate.
                let mut buffer = vec![0u8; max_bytes.min(1024 * 1024)];
                self.inner.try_recv(&mut buffer).map(|count| {
                    count.map(|count| {
                        buffer.truncate(count);
                        buffer
                    })
                })
            })
            .map_err(py_io)?;
        Ok(data.map(|bytes| PyBytes::new(py, &bytes)))
    }

    fn try_send(&self, py: Python<'_>, data: &[u8]) -> PyResult<Option<usize>> {
        py.detach(|| self.inner.try_send(data)).map_err(py_io)
    }

    fn shutdown_write(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| self.inner.shutdown_write()).map_err(py_io)
    }

    fn close(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| self.inner.close()).map_err(py_io)
    }
}
