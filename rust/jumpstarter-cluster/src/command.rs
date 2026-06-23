//! Async subprocess helpers over `tokio::process` — the Rust analog of
//! `cluster/common.py` `run_command` / `run_command_with_output`. Every cluster
//! operation shells out to `kind`/`minikube`/`kubectl`/`helm`/`ssh`/`k3s` through
//! these.

use std::ffi::OsStr;
use std::process::Stdio;
use std::time::{Duration, Instant};

use tokio::io::AsyncWriteExt;
use tokio::process::Command;

use crate::error::{ClusterError, Result};

/// Render a command's argv as a `Vec<String>` for `args = ?` log fields.
fn arg_strings<S: AsRef<OsStr>>(args: &[S]) -> Vec<String> {
    args.iter()
        .map(|a| a.as_ref().to_string_lossy().into_owned())
        .collect()
}

/// Per-command time budget. Cluster ops shell out to kind/minikube/helm/kubectl, which can
/// legitimately run for minutes, so this is generous — it only bounds a *truly hung* command so
/// a wedged tool can't hang the CLI forever. Override with `JMP_CLUSTER_CMD_TIMEOUT_SECS`.
fn command_timeout() -> Duration {
    let secs = std::env::var("JMP_CLUSTER_CMD_TIMEOUT_SECS")
        .ok()
        .and_then(|s| s.parse::<u64>().ok())
        .filter(|s| *s > 0)
        .unwrap_or(900);
    Duration::from_secs(secs)
}

/// Helper: a `Timeout` error for `prog` after `timeout`, logging the kill.
fn timeout_err(prog: &OsStr, timeout: Duration) -> ClusterError {
    let program = prog.to_string_lossy().into_owned();
    tracing::warn!(prog = %program, seconds = timeout.as_secs(), "command timed out; killed");
    ClusterError::Timeout {
        program,
        seconds: timeout.as_secs(),
    }
}

/// The captured result of a command (`(returncode, stdout, stderr)` in Python).
#[derive(Debug, Clone)]
pub struct Output {
    pub code: i32,
    pub stdout: String,
    pub stderr: String,
}

impl Output {
    pub fn ok(&self) -> bool {
        self.code == 0
    }
}

fn spawn_err(prog: &OsStr, e: std::io::Error) -> ClusterError {
    ClusterError::Spawn { program: prog.to_string_lossy().into_owned(), source: e }
}

/// Run a command and capture exit code + stdout + stderr, decoding lossily and
/// trimming both streams (matches Python `.decode(errors="replace").strip()`).
pub async fn run_command<S: AsRef<OsStr>>(cmd: &[S]) -> Result<Output> {
    let (prog, args) = cmd.split_first().ok_or(ClusterError::EmptyCommand)?;
    let prog_str = prog.as_ref().to_string_lossy();
    tracing::debug!(prog = %prog_str, args = ?arg_strings(args), "spawning (capture)");
    let started = Instant::now();
    let timeout = command_timeout();
    let out = match tokio::time::timeout(
        timeout,
        Command::new(prog.as_ref())
            .args(args.iter().map(|a| a.as_ref()))
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .output(),
    )
    .await
    {
        Ok(r) => r.map_err(|e| {
            tracing::warn!(prog = %prog_str, error = %e, "spawn failed");
            spawn_err(prog.as_ref(), e)
        })?,
        Err(_) => return Err(timeout_err(prog.as_ref(), timeout)),
    };
    let code = out.status.code().unwrap_or(-1);
    let elapsed = started.elapsed();
    if code == 0 {
        tracing::debug!(prog = %prog_str, code, elapsed = ?elapsed, "subprocess finished");
    } else {
        tracing::warn!(prog = %prog_str, code, elapsed = ?elapsed, "subprocess exited non-zero");
    }
    Ok(Output {
        code,
        stdout: String::from_utf8_lossy(&out.stdout).trim().to_string(),
        stderr: String::from_utf8_lossy(&out.stderr).trim().to_string(),
    })
}

/// Run a command inheriting the parent's stdio (real-time streaming), returning
/// only the exit code (`run_command_with_output`).
pub async fn run_command_streamed<S: AsRef<OsStr>>(cmd: &[S]) -> Result<i32> {
    let (prog, args) = cmd.split_first().ok_or(ClusterError::EmptyCommand)?;
    let prog_str = prog.as_ref().to_string_lossy();
    tracing::debug!(prog = %prog_str, args = ?arg_strings(args), "spawning (streamed)");
    let started = Instant::now();
    let timeout = command_timeout();
    let status = match tokio::time::timeout(
        timeout,
        Command::new(prog.as_ref())
            .args(args.iter().map(|a| a.as_ref()))
            .stdin(Stdio::inherit())
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .kill_on_drop(true)
            .status(),
    )
    .await
    {
        Ok(r) => r.map_err(|e| {
            tracing::warn!(prog = %prog_str, error = %e, "spawn failed");
            spawn_err(prog.as_ref(), e)
        })?,
        Err(_) => return Err(timeout_err(prog.as_ref(), timeout)),
    };
    let code = status.code().unwrap_or(-1);
    let elapsed = started.elapsed();
    if code == 0 {
        tracing::debug!(prog = %prog_str, code, elapsed = ?elapsed, "subprocess finished");
    } else {
        tracing::warn!(prog = %prog_str, code, elapsed = ?elapsed, "subprocess exited non-zero");
    }
    Ok(code)
}

/// Run a command, feeding `input` to its stdin (for `kubectl apply -f -`).
pub async fn run_command_stdin<S: AsRef<OsStr>>(cmd: &[S], input: &[u8]) -> Result<Output> {
    let (prog, args) = cmd.split_first().ok_or(ClusterError::EmptyCommand)?;
    let prog_str = prog.as_ref().to_string_lossy();
    tracing::debug!(prog = %prog_str, args = ?arg_strings(args), stdin_bytes = input.len(), "spawning (stdin)");
    let started = Instant::now();
    let timeout = command_timeout();
    let mut child = Command::new(prog.as_ref())
        .args(args.iter().map(|a| a.as_ref()))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .map_err(|e| {
            tracing::warn!(prog = %prog_str, error = %e, "spawn failed");
            spawn_err(prog.as_ref(), e)
        })?;
    if let Some(mut stdin) = child.stdin.take() {
        stdin.write_all(input).await.map_err(|e| spawn_err(prog.as_ref(), e))?;
        // Drop stdin to signal EOF.
        drop(stdin);
    }
    // On timeout the `wait_with_output` future (which owns the child) is dropped → `kill_on_drop`
    // terminates the subprocess.
    let out = match tokio::time::timeout(timeout, child.wait_with_output()).await {
        Ok(r) => r.map_err(|e| spawn_err(prog.as_ref(), e))?,
        Err(_) => return Err(timeout_err(prog.as_ref(), timeout)),
    };
    let code = out.status.code().unwrap_or(-1);
    let elapsed = started.elapsed();
    if code == 0 {
        tracing::debug!(prog = %prog_str, code, elapsed = ?elapsed, "subprocess finished");
    } else {
        tracing::warn!(prog = %prog_str, code, elapsed = ?elapsed, "subprocess exited non-zero");
    }
    Ok(Output {
        code,
        stdout: String::from_utf8_lossy(&out.stdout).trim().to_string(),
        stderr: String::from_utf8_lossy(&out.stderr).trim().to_string(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn captures_stdout_and_zero_exit() {
        let out = run_command(&["echo", "hello"]).await.unwrap();
        assert_eq!(out.code, 0);
        assert_eq!(out.stdout, "hello");
        assert!(out.ok());
    }

    #[tokio::test]
    async fn nonzero_exit_is_reported() {
        let out = run_command(&["false"]).await.unwrap();
        assert_ne!(out.code, 0);
        assert!(!out.ok());
    }

    #[tokio::test]
    async fn missing_binary_is_a_spawn_error() {
        let err = run_command(&["definitely-not-a-real-binary-xyz"]).await.unwrap_err();
        assert!(matches!(err, ClusterError::Spawn { .. }));
    }

    #[tokio::test]
    async fn empty_command_errors() {
        let empty: [&str; 0] = [];
        assert!(matches!(run_command(&empty).await.unwrap_err(), ClusterError::EmptyCommand));
    }

    #[tokio::test]
    async fn stdin_is_forwarded() {
        let out = run_command_stdin(&["cat"], b"piped-input").await.unwrap();
        assert_eq!(out.stdout, "piped-input");
    }
}
