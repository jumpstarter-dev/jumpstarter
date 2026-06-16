//! The Python driver-host subprocess (spec 09 §3.2, option a).
//!
//! Spawns `session_host.py`, which serves the `ExporterService` + `RouterService`
//! for an exporter config on a Unix socket and prints its path. The Rust core
//! reads the path, then registers with the controller and bridges the router to
//! this socket. Killing the [`DriverHost`] tears down the session.

use std::path::Path;
use std::process::Stdio;
use std::time::Duration;

use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::time::timeout;

use crate::Error;

/// The embedded Python host script (written to a temp file at spawn time so the
/// Rust binary stays self-contained).
const HELPER: &str = include_str!("../python/session_host.py");

/// Env var selecting the Python interpreter for the driver host. Must have the
/// `jumpstarter` package importable; defaults to `python3`.
const PYTHON_ENV: &str = "JMP_DRIVER_HOST_PYTHON";

/// A running Python session host. Dropping it kills the subprocess.
pub struct DriverHost {
    child: Child,
    socket: String,
}

impl DriverHost {
    /// Spawn the driver host for an exporter config file and wait for it to report
    /// its session socket path (up to 30 s).
    pub async fn spawn(config_path: &Path) -> Result<Self, Error> {
        let script =
            std::env::temp_dir().join(format!("jmp-session-host-{}.py", std::process::id()));
        std::fs::write(&script, HELPER)
            .map_err(|e| Error::Config(format!("writing driver-host helper: {e}")))?;

        let python = std::env::var(PYTHON_ENV).unwrap_or_else(|_| "python3".to_string());
        let mut child = Command::new(&python)
            .arg(&script)
            .arg(config_path)
            .stdout(Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .map_err(|e| Error::Config(format!("spawning driver host ({python}): {e}")))?;

        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| Error::Config("driver host has no stdout".into()))?;
        let mut lines = BufReader::new(stdout).lines();

        let socket = timeout(Duration::from_secs(30), lines.next_line())
            .await
            .map_err(|_| Error::Config("driver host did not report a socket within 30s".into()))?
            .map_err(|e| Error::Config(format!("reading driver-host socket: {e}")))?
            .ok_or_else(|| Error::Config("driver host exited before reporting a socket".into()))?;

        tracing::info!(%socket, "driver host serving session");
        Ok(Self { child, socket })
    }

    /// The Unix socket path the session is served on (also the `JUMPSTARTER_HOST`
    /// the bridge targets).
    pub fn socket(&self) -> &str {
        &self.socket
    }
}

impl Drop for DriverHost {
    fn drop(&mut self) {
        let _ = self.child.start_kill();
    }
}
