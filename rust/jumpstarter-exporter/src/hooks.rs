//! Lifecycle hook execution (spec doc 03; `exporter/hooks.py`).
//!
//! A hook is a `beforeLease`/`afterLease` script run as a subprocess against the
//! session's dedicated **hook socket** (so `j` commands don't contend with client
//! traffic on the main socket). [`run_hook`] executes a hook and maps its `on_failure`
//! policy; the lease runner ([`crate::lease_runner`]) calls it via
//! [`crate::controller_effects`], reporting the `*_HOOK_FAILED`/`OFFLINE` failure statuses,
//! while the FSM projection drives the clean-phase `BEFORE_LEASE_HOOK` → `LEASE_READY`,
//! `AFTER_LEASE_HOOK` → `AVAILABLE` sequence. (`run_before_lease`/`run_after_lease` remain
//! for standalone serving.)

use std::path::Path;
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use jumpstarter_config::{HookInstanceConfig, OnFailure};
use jumpstarter_protocol::v1::{ExporterStatus, LogSource};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Command;
use tokio::time::timeout;

use crate::control::StatusReporter;
use crate::driver_host;
use crate::logbuf::HookLog;

/// Prefix on warn-mode status messages (`common/__init__.py:12`).
const HOOK_WARNING_PREFIX: &str = "[HOOK_WARNING] ";

/// Inputs every hook subprocess needs.
pub struct HookContext<'a> {
    pub hook_socket: &'a str,
    pub lease_name: &'a str,
    pub client_name: &'a str,
    /// Sink for the hook's output, so a client `--exporter-logs` LogStream sees it.
    pub hook_log: Arc<HookLog>,
}

/// The decision a `beforeLease` hook produces.
#[derive(Debug, PartialEq, Eq)]
pub enum BeforeOutcome {
    /// Proceed to `Ready` (LEASE_READY reported).
    Ready,
    /// End the lease without serving (`on_failure: endLease`). The client never reached
    /// `LEASE_READY`, so it never connected (`has_client` stays false) → afterLease is skipped, and
    /// the lease is released (`request_release`, since the end reason isn't `Controller`). Matches
    /// the Python behaviour from #823 (release the lease + skip afterLease on beforeLease endLease).
    EndLease,
    /// Shut the exporter down (`on_failure: exit`); skip afterLease.
    Exit,
}

/// The decision an `afterLease` hook produces.
#[derive(Debug, PartialEq, Eq)]
pub enum AfterOutcome {
    Done,
    /// Shut the exporter down (`on_failure: exit`).
    Exit,
}

/// The raw result of running a hook, after applying its `on_failure` policy. Exposed to the
/// lease runner's `ControllerEffects`, which runs the hook subprocess and reports the failure
/// statuses itself (the runner drives the clean-phase statuses via the FSM projection).
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum HookOutcome {
    Success,
    Warn(String),
    EndLease(String),
    Exit(String),
}

/// Run the `beforeLease` hook (or none) and report the resulting status.
pub async fn run_before_lease(
    reporter: &mut StatusReporter,
    hook: Option<&HookInstanceConfig>,
    ctx: &HookContext<'_>,
) -> BeforeOutcome {
    let Some(hook) = hook else {
        reporter
            .report(ExporterStatus::LeaseReady, "Ready for commands")
            .await;
        return BeforeOutcome::Ready;
    };

    reporter
        .report(ExporterStatus::BeforeLeaseHook, "Running beforeLease hook")
        .await;
    match run_hook(hook, ctx, LogSource::BeforeLeaseHook).await {
        HookOutcome::Success => {
            reporter
                .report(ExporterStatus::LeaseReady, "Ready for commands")
                .await;
            BeforeOutcome::Ready
        }
        HookOutcome::Warn(w) => {
            let msg = format!("{HOOK_WARNING_PREFIX}beforeLease hook warning: {w}");
            reporter.report(ExporterStatus::LeaseReady, &msg).await;
            BeforeOutcome::Ready
        }
        HookOutcome::EndLease(e) => {
            let msg = format!("beforeLease hook failed (on_failure=endLease): {e}");
            reporter
                .report(ExporterStatus::BeforeLeaseHookFailed, &msg)
                .await;
            BeforeOutcome::EndLease
        }
        HookOutcome::Exit(e) => {
            let msg = format!("beforeLease hook failed (on_failure=exit, shutting down): {e}");
            reporter
                .report(ExporterStatus::BeforeLeaseHookFailed, &msg)
                .await;
            reporter
                .report(
                    ExporterStatus::Offline,
                    "Exporter shutting down due to beforeLease hook failure",
                )
                .await;
            BeforeOutcome::Exit
        }
    }
}

/// Run the `afterLease` hook (or none) and report the resulting status.
pub async fn run_after_lease(
    reporter: &mut StatusReporter,
    hook: Option<&HookInstanceConfig>,
    ctx: &HookContext<'_>,
) -> AfterOutcome {
    let Some(hook) = hook else {
        reporter
            .report(ExporterStatus::Available, "Available for new lease")
            .await;
        return AfterOutcome::Done;
    };

    reporter
        .report(ExporterStatus::AfterLeaseHook, "Running afterLease hooks")
        .await;
    match run_hook(hook, ctx, LogSource::AfterLeaseHook).await {
        HookOutcome::Success => {
            reporter
                .report(ExporterStatus::Available, "Available for new lease")
                .await;
            AfterOutcome::Done
        }
        HookOutcome::Warn(w) => {
            let msg = format!("{HOOK_WARNING_PREFIX}afterLease hook warning: {w}");
            // Also emit on the log stream (replay-buffered, so the client reliably
            // sees it): the status message is overwritten by the subsequent
            // `request_release` Available report, so a `GetStatus` poll can miss it.
            ctx.hook_log.push(LogSource::AfterLeaseHook, msg.clone());
            reporter.report(ExporterStatus::Available, &msg).await;
            AfterOutcome::Done
        }
        HookOutcome::EndLease(e) => {
            let msg = format!("afterLease hook failed (on_failure=endLease): {e}");
            reporter
                .report(ExporterStatus::AfterLeaseHookFailed, &msg)
                .await;
            AfterOutcome::Done
        }
        HookOutcome::Exit(e) => {
            let msg = format!("afterLease hook failed (on_failure=exit, shutting down): {e}");
            reporter
                .report(ExporterStatus::AfterLeaseHookFailed, &msg)
                .await;
            reporter
                .report(
                    ExporterStatus::Offline,
                    "Exporter shutting down due to afterLease hook failure",
                )
                .await;
            AfterOutcome::Exit
        }
    }
}

/// Execute a hook and map its result through the configured `on_failure` policy.
pub(crate) async fn run_hook(
    hook: &HookInstanceConfig,
    ctx: &HookContext<'_>,
    source: LogSource,
) -> HookOutcome {
    match execute(hook, ctx, source).await {
        Ok(()) => HookOutcome::Success,
        Err(message) => match hook.on_failure {
            OnFailure::Warn => HookOutcome::Warn(message),
            OnFailure::EndLease => HookOutcome::EndLease(message),
            OnFailure::Exit => HookOutcome::Exit(message),
        },
    }
}

/// The interpreter + args to invoke a hook with (`hooks.py:272-296`): an explicit
/// `exec`, else auto-detected from a script file's extension (`.py` → the
/// driver-host Python, else `/bin/sh`), else `/bin/sh -c <inline>`.
fn build_command(hook: &HookInstanceConfig) -> (String, Vec<String>) {
    let script = hook.script.trim();
    let is_file = !script.contains('\n') && Path::new(script).is_file();

    let interpreter = hook.exec.clone().unwrap_or_else(|| {
        if is_file && Path::new(script).extension().and_then(|e| e.to_str()) == Some("py") {
            driver_host::python_interpreter()
        } else {
            "/bin/sh".to_string()
        }
    });

    let args = if is_file {
        vec![script.to_string()]
    } else {
        vec!["-c".to_string(), hook.script.clone()]
    };
    (interpreter, args)
}

/// Spawn the hook subprocess with the env contract, stream its output to the log,
/// and enforce the timeout. Returns `Err(message)` on non-zero exit, spawn error,
/// or timeout.
async fn execute(
    hook: &HookInstanceConfig,
    ctx: &HookContext<'_>,
    source: LogSource,
) -> Result<(), String> {
    let (interpreter, args) = build_command(hook);

    let mut cmd = Command::new(&interpreter);
    cmd.args(&args)
        // Hook environment contract (hooks.py:123-139).
        .env("JUMPSTARTER_HOST", ctx.hook_socket)
        .env("JMP_DRIVERS_ALLOW", "UNSAFE")
        .env("LEASE_NAME", ctx.lease_name)
        .env("CLIENT_NAME", ctx.client_name)
        .env("TERM", "dumb")
        .env("DEBIAN_FRONTEND", "noninteractive")
        .env("GIT_TERMINAL_PROMPT", "0")
        .env_remove("PS1")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("Error executing hook ({interpreter}): {e}"))?;

    // Drain stdout/stderr concurrently with waiting so a chatty hook can't fill the
    // pipe buffer and deadlock against `child.wait()`. Each line is logged and pushed
    // to the hook-log buffer (so `--exporter-logs` surfaces it).
    let out_drain = child
        .stdout
        .take()
        .map(|s| tokio::spawn(log_lines(s, ctx.hook_log.clone(), source)));
    let err_drain = child
        .stderr
        .take()
        .map(|s| tokio::spawn(log_lines(s, ctx.hook_log.clone(), source)));

    let result = match timeout(
        Duration::from_secs(hook.timeout.max(0) as u64),
        child.wait(),
    )
    .await
    {
        Ok(Ok(status)) if status.success() => Ok(()),
        Ok(Ok(status)) => Err(format!(
            "Hook failed with exit code {}",
            status
                .code()
                .map(|c| c.to_string())
                .unwrap_or_else(|| "terminated by signal".to_string())
        )),
        Ok(Err(e)) => Err(format!("Error executing hook: {e}")),
        Err(_) => {
            let _ = child.start_kill();
            Err(format!("Hook timed out after {} seconds", hook.timeout))
        }
    };

    // Flush any buffered output before returning.
    if let Some(t) = out_drain {
        let _ = t.await;
    }
    if let Some(t) = err_drain {
        let _ = t.await;
    }
    result
}

/// Read a child stream line by line, forwarding each to the tracing log and the
/// hook-log buffer (tagged with `source`).
async fn log_lines<R>(reader: R, hook_log: Arc<HookLog>, source: LogSource)
where
    R: tokio::io::AsyncRead + Unpin + Send + 'static,
{
    let mut lines = BufReader::new(reader).lines();
    while let Ok(Some(line)) = lines.next_line().await {
        if !line.trim().is_empty() {
            tracing::info!(target: "jumpstarter_exporter::hook", "{line}");
            hook_log.push(source, line);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hook(script: &str, on_failure: OnFailure) -> HookInstanceConfig {
        HookInstanceConfig {
            exec: None,
            script: script.to_string(),
            timeout: 30,
            on_failure,
        }
    }

    fn ctx() -> HookContext<'static> {
        HookContext {
            hook_socket: "/tmp/unused.sock",
            lease_name: "lease-1",
            client_name: "client-1",
            hook_log: HookLog::new(),
        }
    }

    #[test]
    fn inline_script_uses_sh_dash_c() {
        let (interp, args) = build_command(&hook("j power on", OnFailure::Warn));
        assert_eq!(interp, "/bin/sh");
        assert_eq!(args, vec!["-c".to_string(), "j power on".to_string()]);
    }

    #[test]
    fn exec_override_is_respected() {
        let mut h = hook("print('hi')", OnFailure::Warn);
        h.exec = Some("python3".to_string());
        let (interp, args) = build_command(&h);
        assert_eq!(interp, "python3");
        assert_eq!(args, vec!["-c".to_string(), "print('hi')".to_string()]);
    }

    #[test]
    fn script_file_detected_by_extension() {
        let dir = std::env::temp_dir();
        let py = dir.join(format!("jmp-hook-test-{}.py", std::process::id()));
        let sh = dir.join(format!("jmp-hook-test-{}.sh", std::process::id()));
        std::fs::write(&py, "print('hi')").unwrap();
        std::fs::write(&sh, "echo hi").unwrap();

        let (interp, args) = build_command(&hook(py.to_str().unwrap(), OnFailure::Warn));
        assert_eq!(interp, driver_host::python_interpreter());
        assert_eq!(args, vec![py.to_string_lossy().to_string()]);

        let (interp, _) = build_command(&hook(sh.to_str().unwrap(), OnFailure::Warn));
        assert_eq!(interp, "/bin/sh");

        let _ = std::fs::remove_file(py);
        let _ = std::fs::remove_file(sh);
    }

    #[tokio::test]
    async fn successful_hook_is_success() {
        assert_eq!(
            run_hook(
                &hook("exit 0", OnFailure::Exit),
                &ctx(),
                LogSource::BeforeLeaseHook
            )
            .await,
            HookOutcome::Success
        );
    }

    #[tokio::test]
    async fn failing_hook_maps_through_on_failure() {
        match run_hook(
            &hook("exit 3", OnFailure::Warn),
            &ctx(),
            LogSource::BeforeLeaseHook,
        )
        .await
        {
            HookOutcome::Warn(m) => assert!(m.contains("exit code 3")),
            other => panic!("expected Warn, got {other:?}"),
        }
        assert!(matches!(
            run_hook(
                &hook("exit 1", OnFailure::EndLease),
                &ctx(),
                LogSource::BeforeLeaseHook
            )
            .await,
            HookOutcome::EndLease(_)
        ));
        assert!(matches!(
            run_hook(
                &hook("exit 1", OnFailure::Exit),
                &ctx(),
                LogSource::BeforeLeaseHook
            )
            .await,
            HookOutcome::Exit(_)
        ));
    }

    #[tokio::test]
    async fn timeout_is_a_failure() {
        let mut h = hook("sleep 5", OnFailure::Warn);
        h.timeout = 1;
        match run_hook(&h, &ctx(), LogSource::BeforeLeaseHook).await {
            HookOutcome::Warn(m) => assert!(m.contains("timed out")),
            other => panic!("expected timeout Warn, got {other:?}"),
        }
    }
}
