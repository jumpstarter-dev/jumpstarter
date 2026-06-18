//! The Python driver-host subprocess (spec 09 §3.2, option a; native-migration
//! design `rust/docs/03-native-exporter-migration.md`).
//!
//! Two hosts coexist during the native migration:
//!
//! - [`DriverHost`] spawns `session_host.py`, which serves the full
//!   `ExporterService` + `RouterService` for the config on a main *and* hook socket.
//!   This is the live path until the Rust core serves the protocol itself.
//! - [`SlimHost`] spawns `slim_driver_host.py`, which serves the *driver-level* RPCs
//!   for the whole tree on a **single** private socket; the Rust core terminates the
//!   client/hook protocol and proxies driver calls into it by UUID.
//!
//! Both print their socket path(s) on stdout and are torn down when dropped.

use std::path::Path;
use std::process::Stdio;
use std::time::Duration;

use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, ChildStdout, Command};
use tokio::time::timeout;

use crate::Error;

/// The embedded Python host scripts (written to temp files at spawn time so the Rust
/// binary stays self-contained).
const HELPER: &str = include_str!("../python/session_host.py");
const SLIM_HELPER: &str = include_str!("../python/slim_driver_host.py");

/// Env var selecting the Python interpreter for the driver host (and for `.py`
/// hooks). Must have the `jumpstarter` package importable; defaults to `python3`.
const PYTHON_ENV: &str = "JMP_DRIVER_HOST_PYTHON";

/// How long to wait for a driver host to report its socket path(s).
const STARTUP_TIMEOUT: Duration = Duration::from_secs(30);

type StdoutLines = tokio::io::Lines<BufReader<ChildStdout>>;

/// The Python interpreter to invoke for the driver host and `.py` hooks.
pub(crate) fn python_interpreter() -> String {
    std::env::var(PYTHON_ENV).unwrap_or_else(|_| "python3".to_string())
}

/// A running dual-socket Python session host (`session_host.py`). Dropping it kills
/// the subprocess.
pub struct DriverHost {
    child: Child,
    main_socket: String,
    hook_socket: String,
}

impl DriverHost {
    /// Spawn the driver host for an exporter config file and wait for it to report
    /// its main and hook socket paths (up to [`STARTUP_TIMEOUT`]).
    pub async fn spawn(config_path: &Path) -> Result<Self, Error> {
        let (child, mut lines) = spawn_host("session-host", HELPER, config_path)?;

        let (main_socket, hook_socket) = timeout(STARTUP_TIMEOUT, async {
            let main = next_socket_line(&mut lines).await?;
            let hook = next_socket_line(&mut lines).await?;
            Ok::<_, Error>((main, hook))
        })
        .await
        .map_err(|_| Error::Config("driver host did not report its sockets within 30s".into()))??;

        drain_stdout(lines);
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

impl Drop for DriverHost {
    fn drop(&mut self) {
        let _ = self.child.start_kill();
    }
}

/// A running slim driver host (`slim_driver_host.py`): the whole driver tree on a
/// single private socket, serving driver-level RPCs to the Rust core only. Dropping
/// it kills the subprocess.
pub struct SlimHost {
    child: Child,
    socket: String,
}

impl SlimHost {
    /// Spawn the slim host for an exporter config file and wait for its single socket
    /// path (up to [`STARTUP_TIMEOUT`]).
    pub async fn spawn(config_path: &Path) -> Result<Self, Error> {
        let (child, mut lines) = spawn_host("slim-host", SLIM_HELPER, config_path)?;

        let socket = timeout(STARTUP_TIMEOUT, next_socket_line(&mut lines))
            .await
            .map_err(|_| {
                Error::Config("slim host did not report its socket within 30s".into())
            })??;

        drain_stdout(lines);
        tracing::info!(%socket, "slim driver host serving session");
        Ok(Self { child, socket })
    }

    /// The single private socket serving the whole tree's driver-level RPCs.
    pub fn socket(&self) -> &str {
        &self.socket
    }
}

impl Drop for SlimHost {
    fn drop(&mut self) {
        let _ = self.child.start_kill();
    }
}

/// Write an embedded helper to a temp file and spawn the configured Python
/// interpreter on it, returning the child and a line reader over its stdout.
fn spawn_host(
    prefix: &str,
    helper: &str,
    config_path: &Path,
) -> Result<(Child, StdoutLines), Error> {
    let script = std::env::temp_dir().join(format!("jmp-{prefix}-{}.py", std::process::id()));
    std::fs::write(&script, helper)
        .map_err(|e| Error::Config(format!("writing {prefix} helper: {e}")))?;

    let python = python_interpreter();
    let mut child = Command::new(&python)
        .arg(&script)
        .arg(config_path)
        .stdout(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .map_err(|e| Error::Config(format!("spawning {prefix} ({python}): {e}")))?;

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| Error::Config(format!("{prefix} has no stdout")))?;
    Ok((child, BufReader::new(stdout).lines()))
}

/// Read the next non-empty line, mapping EOF to a descriptive error.
async fn next_socket_line(lines: &mut StdoutLines) -> Result<String, Error> {
    lines
        .next_line()
        .await
        .map_err(|e| Error::Config(format!("reading driver-host socket: {e}")))?
        .ok_or_else(|| Error::Config("driver host exited before reporting its socket(s)".into()))
}

/// Forward any further driver-host stdout to the log instead of letting the pipe
/// fill (stderr is inherited, so Python logs are already visible).
fn drain_stdout(mut lines: StdoutLines) {
    tokio::spawn(async move {
        while let Ok(Some(line)) = lines.next_line().await {
            tracing::debug!(target: "jumpstarter_exporter::driver_host", "{line}");
        }
    });
}
