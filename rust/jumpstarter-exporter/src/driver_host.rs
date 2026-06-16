//! The Python driver-host subprocess (spec 09 §3.2, option a).
//!
//! Spawns `session_host.py`, which serves the `ExporterService` + `RouterService`
//! for an exporter config on two Unix sockets (a main client socket and an isolated
//! hook socket) and prints both paths. The Rust core reads the paths, registers with
//! the controller, bridges the router to the main socket, and points hook
//! subprocesses at the hook socket. Killing the [`DriverHost`] tears down the session.

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

/// Env var selecting the Python interpreter for the driver host (and for `.py`
/// hooks). Must have the `jumpstarter` package importable; defaults to `python3`.
const PYTHON_ENV: &str = "JMP_DRIVER_HOST_PYTHON";

/// How long to wait for the driver host to report both of its socket paths.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(30);

/// The Python interpreter to invoke for the driver host and `.py` hooks.
pub(crate) fn python_interpreter() -> String {
    std::env::var(PYTHON_ENV).unwrap_or_else(|_| "python3".to_string())
}

/// A running Python session host. Dropping it kills the subprocess.
pub struct DriverHost {
    child: Child,
    main_socket: String,
    hook_socket: String,
}

impl DriverHost {
    /// Spawn the driver host for an exporter config file and wait for it to report
    /// its main and hook socket paths (up to [`STARTUP_TIMEOUT`]).
    pub async fn spawn(config_path: &Path) -> Result<Self, Error> {
        let script =
            std::env::temp_dir().join(format!("jmp-session-host-{}.py", std::process::id()));
        std::fs::write(&script, HELPER)
            .map_err(|e| Error::Config(format!("writing driver-host helper: {e}")))?;

        let python = python_interpreter();
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

        let (main_socket, hook_socket) = timeout(STARTUP_TIMEOUT, async {
            let main = next_socket_line(&mut lines).await?;
            let hook = next_socket_line(&mut lines).await?;
            Ok::<_, Error>((main, hook))
        })
        .await
        .map_err(|_| Error::Config("driver host did not report its sockets within 30s".into()))??;

        // Forward any further driver-host stdout to the log instead of letting the
        // pipe fill (stderr is inherited, so Python logs are already visible).
        tokio::spawn(async move {
            while let Ok(Some(line)) = lines.next_line().await {
                tracing::debug!(target: "jumpstarter_exporter::driver_host", "{line}");
            }
        });

        tracing::info!(%main_socket, %hook_socket, "driver host serving session");
        Ok(Self {
            child,
            main_socket,
            hook_socket,
        })
    }

    /// The main session socket: where the router bridge and clients connect (the
    /// `JUMPSTARTER_HOST` for client traffic).
    pub fn main_socket(&self) -> &str {
        &self.main_socket
    }

    /// The isolated hook socket: the `JUMPSTARTER_HOST` for hook `j` commands.
    pub fn hook_socket(&self) -> &str {
        &self.hook_socket
    }
}

/// Read the next non-empty line, mapping EOF to a descriptive error.
async fn next_socket_line<R>(lines: &mut tokio::io::Lines<R>) -> Result<String, Error>
where
    R: AsyncBufReadExt + Unpin,
{
    lines
        .next_line()
        .await
        .map_err(|e| Error::Config(format!("reading driver-host socket: {e}")))?
        .ok_or_else(|| Error::Config("driver host exited before reporting its sockets".into()))
}

impl Drop for DriverHost {
    fn drop(&mut self) {
        let _ = self.child.start_kill();
    }
}
