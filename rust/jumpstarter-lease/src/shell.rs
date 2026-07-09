//! `jmp shell` orchestration: acquire a lease, serve the transport host, run a
//! shell/command wired to it, and release the lease on exit
//! (`common/utils.py:launch_shell`, `jumpstarter-cli/.../shell.py`).
//!
//! The child inherits stdio (so an interactive shell works) and the established
//! environment contract: `JUMPSTARTER_HOST`, `JMP_DRIVERS_ALLOW`
//! (`"UNSAFE"` or the comma-joined allow-list), and `_JMP_SUPPRESS_DRIVER_WARNINGS`.

use std::time::{Duration, Instant};

use jumpstarter_config::ClientConfig;
use jumpstarter_protocol::v1::exporter_service_client::ExporterServiceClient;
use jumpstarter_protocol::v1::{EndSessionRequest, ExporterStatus, GetStatusRequest};
use tracing::{debug, info, trace, warn};

use crate::error::{ClientError, LeaseError};
use crate::lease::{acquire, CreateLeaseParams, LeaseProvider, LeaseTiming};
use crate::service::ControllerClient;
use crate::transport;

/// Prefix the exporter puts on a `GetStatus` message when a hook failed with
/// `on_failure: warn` (`common/__init__.py:HOOK_WARNING_PREFIX`). The client strips
/// it and prints `Warning: …` (`shell.py:323`).
const HOOK_WARNING_PREFIX: &str = "[HOOK_WARNING] ";

/// If `message` is a hook warning, print it as `Warning: …` (mirrors `shell.py`'s
/// yellow warning). Returns whether it printed, so callers print each warning once.
fn print_hook_warning(message: &Option<String>) -> bool {
    if let Some(text) = message
        .as_deref()
        .and_then(|m| m.strip_prefix(HOOK_WARNING_PREFIX))
    {
        eprintln!("Warning: {text}");
        true
    } else {
        false
    }
}

/// Options for [`run`].
#[derive(Debug, Clone)]
pub struct ShellOptions {
    /// Label selector for the lease.
    pub selector: Option<String>,
    /// Specific exporter name (instead of a selector).
    pub exporter_name: Option<String>,
    /// Reuse an existing lease by name (`--lease`/`JMP_LEASE`). When set, the lease
    /// is *not* released on exit so it can be reconnected to.
    pub lease_name: Option<String>,
    /// Lease duration.
    pub duration: Duration,
    /// Override the lease-acquisition timeout (`--acquisition-timeout`).
    pub acquisition_timeout: Option<Duration>,
    /// Stream *all* exporter logs (driver + system) during the session. Hook logs
    /// (before/afterLease) are always streamed regardless (`--exporter-logs`).
    pub exporter_logs: bool,
    /// Command to run instead of an interactive `$SHELL`.
    pub command: Option<Vec<String>>,
}

impl Default for ShellOptions {
    fn default() -> Self {
        Self {
            selector: None,
            exporter_name: None,
            lease_name: None,
            duration: Duration::from_secs(30 * 60),
            acquisition_timeout: None,
            exporter_logs: false,
            command: None,
        }
    }
}

/// Acquire a lease for `config`, serve the transport socket, run the shell/command,
/// release the lease (only if we created it), and return the child's exit code.
pub async fn run(config: &ClientConfig, opts: ShellOptions) -> Result<i32, ClientError> {
    let client = ControllerClient::connect(config).await?;

    // A reused lease (`--lease`/resolved-from-active) is not released on exit
    // (`shell.py`: `lease.release` is False for pre-created leases).
    let release = opts.lease_name.is_none();
    let timing = LeaseTiming {
        acquisition_timeout: opts
            .acquisition_timeout
            .unwrap_or_else(|| LeaseTiming::default().acquisition_timeout),
        ..LeaseTiming::default()
    };

    let acquired = match acquire(
        &client,
        CreateLeaseParams {
            selector: opts.selector.clone(),
            exporter_name: opts.exporter_name.clone(),
            duration: opts.duration,
            ..Default::default()
        },
        opts.lease_name.clone(),
        Some(&config.metadata.name),
        timing,
    )
    .await
    {
        Ok(acquired) => acquired,
        // A lease that is `Released` before it is ever observed `Ready` means the
        // exporter took the lease and gave it back during setup — most commonly a
        // `beforeLease` hook failing with `onFailure: endLease`, which ends the
        // lease faster than the acquisition poll catches the brief `Ready=True`
        // window (a fast controller widens this race; the exporter/lease genuinely
        // went away). Surface it like the beforeLease-wait's own connection-loss
        // path (see `wait_for_lease_ready`) rather than the raw "lease released",
        // so the user sees why the shell could not start.
        Err(ClientError::Lease(LeaseError::Released(name))) => {
            warn!(
                lease = %name,
                "lease released before it became ready; treating as connection loss"
            );
            return Err(ClientError::Exporter(
                "Connection to exporter lost".to_string(),
            ));
        }
        Err(e) => return Err(e),
    };

    // The exporter name labels the shell prompt + the lifecycle messages (`shell.py`
    // passes `lease.exporter_name` to `launch_shell`); fall back to the lease name.
    let context = if acquired.exporter.is_empty() {
        acquired.name.clone()
    } else {
        acquired.exporter.clone()
    };
    info!(lease = %acquired.name, exporter = %context, "acquired lease");
    eprintln!("Acquired lease {} on exporter {context}", acquired.name);

    // Exporter context for the shell (#53): fetch the exporter's labels so the child can read
    // `JMP_EXPORTER`/`JMP_LEASE`/`JMP_EXPORTER_LABELS` via `env_with_metadata()`. A fetch failure is
    // non-fatal (empty labels), mirroring `lease.py::_fetch_exporter_labels`.
    let exporter_labels = if acquired.exporter.is_empty() {
        String::new()
    } else {
        debug!(exporter = %acquired.exporter, "fetching exporter labels");
        match client.get_exporter(&acquired.exporter).await {
            Ok(e) => {
                let mut pairs: Vec<(String, String)> = e.labels.into_iter().collect();
                pairs.sort();
                let labels = pairs
                    .into_iter()
                    .map(|(k, v)| format!("{k}={v}"))
                    .collect::<Vec<_>>()
                    .join(",");
                debug!(exporter = %acquired.exporter, labels = %labels, "fetched exporter labels");
                labels
            }
            Err(e) => {
                warn!(exporter = %acquired.exporter, error = %e, "could not fetch exporter labels");
                String::new()
            }
        }
    };
    let lease_env = LeaseEnv {
        exporter: acquired.exporter.clone(),
        lease: acquired.name.clone(),
        labels: exporter_labels,
    };

    let host =
        transport::serve_default(client.clone(), acquired.name.clone(), config.tls.clone()).await?;
    let socket = host.jumpstarter_host();

    let drivers_allow = if config.drivers.r#unsafe {
        "UNSAFE".to_string()
    } else {
        config.drivers.allow.join(",")
    };
    let insecure = crate::channel::is_insecure(&config.tls);

    // Stream exporter logs for the whole session (`shell.py:log_stream_async`): hook
    // (before/afterLease) output is always shown; driver/system only with
    // `--exporter-logs`. Connect via the local transport proxy socket and give the
    // beforeLease replay a moment to flush before the command's own output.
    let log_task = crate::exporter_logs::spawn_controller(socket.clone(), opts.exporter_logs);

    // Wait for the beforeLease hook to finish (LEASE_READY) before running the
    // command, so a beforeLease failure surfaces as a clear error instead of the
    // child's opaque connection timeout (`shell.py`: wait for LEASE_READY /
    // BEFORE_LEASE_HOOK_FAILED via the status monitor, before running the command).
    eprintln!("Waiting for beforeLease hook to complete...");
    if let Err(e) = wait_for_lease_ready(&socket).await {
        warn!(lease = %acquired.name, error = %e, "beforeLease wait failed; tearing down session");
        log_task.abort();
        if release {
            debug!(lease = %acquired.name, "releasing lease after beforeLease failure");
            eprintln!("Releasing lease {}", acquired.name);
            if let Err(err) = client.delete_lease(&acquired.name).await {
                log_release_outcome(&acquired.name, &err);
            }
        }
        drop(host);
        return Err(e);
    }
    debug!(lease = %acquired.name, "beforeLease hook complete (lease ready)");

    let result = spawn_child(
        &opts.command,
        &socket,
        &context,
        &drivers_allow,
        insecure,
        None,
        Some(&lease_env),
    )
    .await;

    // End the session so the afterLease hook runs while the log stream is still open,
    // and wait for it to finish before releasing the lease (`shell.py`: EndSession +
    // wait-for-afterLease, then release). Only for leases we created; a reused lease
    // (`--lease`) is left intact for reconnection.
    if release {
        debug!(lease = %acquired.name, "ending session and running afterLease hook");
        eprintln!("Running afterLease hook (Ctrl+C to skip)...");
        end_session_and_wait(&socket).await;
        debug!(lease = %acquired.name, "afterLease hook completed");
        eprintln!("afterLease hook completed");
    }
    // Brief flush window for trailing afterLease lines, then stop streaming.
    tokio::time::sleep(Duration::from_millis(200)).await;
    log_task.abort();

    if release {
        debug!(lease = %acquired.name, "releasing lease");
        eprintln!("Releasing lease {}", acquired.name);
        if let Err(err) = client.delete_lease(&acquired.name).await {
            log_release_outcome(&acquired.name, &err);
        }
    }
    drop(host);

    result
}

/// Wait for the lease to become ready (`LEASE_READY`) over a *single, persistent*
/// transport-proxy connection, returning an error if the `beforeLease` hook fails.
///
/// Establishing the connection performs the controller `Dial`, which the controller
/// retries server-side only while the exporter is `Available`
/// (`controller_service.go:Dial`); a successful connect therefore means the exporter
/// already went active (`BeforeLeaseHook`+). After that, `GetStatus` on the same
/// connection drives the decision:
/// - `LEASE_READY` → `Ok` (run the command);
/// - `BeforeLeaseHookFailed` (current or `previous_status`) → the exporter's message;
/// - back to `Available`/`Offline`, or the connection drops → the lease ended without
///   becoming ready (endLease, or `onFailure: exit` shut the exporter down).
///
/// Reusing one connection is essential: reconnecting per poll would re-trigger the
/// controller's 30 s `Available` Dial-retry once the lease ends and hang the shell.
async fn wait_for_lease_ready(socket: &str) -> Result<(), ClientError> {
    const RPC_TIMEOUT: Duration = Duration::from_secs(5);
    // Bounds the initial connect (covers the controller's ~30 s Available retry).
    const CONNECT_TIMEOUT: Duration = Duration::from_secs(40);
    let connect = tokio::time::timeout(
        CONNECT_TIMEOUT,
        crate::exporter_logs::uds_channel(socket.to_string()),
    );
    let channel = match connect.await {
        Ok(Ok(ch)) => ch,
        // Never became reachable — the exporter went offline / shut down (e.g. a
        // beforeLease `onFailure: exit`) before serving.
        _ => {
            return Err(ClientError::Exporter(
                "Connection to exporter lost".to_string(),
            ))
        }
    };
    // The connect succeeded, so the exporter reached an active (BeforeLeaseHook+)
    // state; a later drop/return-to-Available means the lease ended.
    let mut client = ExporterServiceClient::new(channel);

    // beforeLease hooks can be slow (their own timeout defaults higher than 120s); give the wait
    // a generous budget so a legitimately slow hook isn't cut off.
    let deadline = Instant::now() + Duration::from_secs(300);
    while Instant::now() < deadline {
        let resp =
            match tokio::time::timeout(RPC_TIMEOUT, client.get_status(GetStatusRequest {})).await {
                Ok(Ok(resp)) => resp.into_inner(),
                // Connection dropped / RPC errored — the session ended before becoming ready.
                _ => {
                    return Err(ClientError::Exporter(
                        "Connection to exporter lost".to_string(),
                    ))
                }
            };
        let status = ExporterStatus::try_from(resp.status).unwrap_or(ExporterStatus::Unspecified);
        let prev = resp
            .previous_status
            .and_then(|p| ExporterStatus::try_from(p).ok());

        if status == ExporterStatus::LeaseReady {
            // beforeLease failed with `on_failure: warn` — show the warning, proceed.
            print_hook_warning(&resp.message);
            return Ok(());
        }
        if status == ExporterStatus::BeforeLeaseHookFailed
            || prev == Some(ExporterStatus::BeforeLeaseHookFailed)
        {
            let msg = (status == ExporterStatus::BeforeLeaseHookFailed)
                .then(|| resp.message.clone())
                .flatten()
                .filter(|m| !m.is_empty())
                .unwrap_or_else(|| "beforeLease hook failed".to_string());
            return Err(ClientError::Exporter(msg));
        }
        // The lease went active then returned to Available/Offline: it ended without
        // ever becoming ready.
        if matches!(status, ExporterStatus::Available | ExporterStatus::Offline) {
            return Err(ClientError::Exporter(
                "Connection to exporter lost".to_string(),
            ));
        }
        // BeforeLeaseHook / transitional — keep polling on the same connection.
        tokio::time::sleep(Duration::from_millis(150)).await;
    }
    // The budget elapsed without ever reaching LEASE_READY: do NOT silently proceed to run the
    // command as if the lease were ready — surface a clear timeout.
    warn!("timed out waiting for beforeLease hook to complete; the lease never became ready");
    Err(ClientError::Exporter(
        "Timed out waiting for beforeLease hook to complete".to_string(),
    ))
}

/// Ask the exporter to end the session (running the `afterLease` hook) and poll
/// `GetStatus` until it completes — so afterLease output streams to the still-open
/// log task before the lease is released (`shell.py:_run_shell_with_lease_async`).
///
/// Best-effort: every RPC is bounded by a timeout (a half-open tunnel must not hang
/// the shell), and the whole wait is capped. On any failure we return and let the
/// normal lease release proceed.
async fn end_session_and_wait(socket: &str) {
    const RPC_TIMEOUT: Duration = Duration::from_secs(5);

    let connect = tokio::time::timeout(
        RPC_TIMEOUT,
        crate::exporter_logs::uds_channel(socket.to_string()),
    );
    let channel = match connect.await {
        Ok(Ok(ch)) => ch,
        Ok(Err(e)) => {
            debug!(error = %e, "end_session: could not connect to exporter; skipping afterLease wait");
            return;
        }
        Err(_elapsed) => {
            debug!("end_session: connect to exporter timed out; skipping afterLease wait");
            return;
        }
    };
    let mut client = ExporterServiceClient::new(channel);

    match tokio::time::timeout(RPC_TIMEOUT, client.end_session(EndSessionRequest {})).await {
        Ok(Ok(_)) => debug!("EndSession requested; waiting for afterLease to complete"),
        // EndSession unsupported/unreachable — nothing to wait for.
        Ok(Err(status)) => {
            debug!(code = ?status.code(), "EndSession RPC errored; skipping afterLease wait");
            return;
        }
        Err(_elapsed) => {
            warn!("EndSession timed out; skipping afterLease wait");
            return;
        }
    }

    // afterLease can be slow (hooks have their own timeouts); bound the overall wait
    // but let the exporter drive completion. Done when it returns to Available/Offline
    // or a hook reaches a terminal failed state. An afterLease `on_failure: warn`
    // warning is delivered on the log stream (the status message is overwritten by the
    // trailing `request_release` Available report, so a poll here can miss it) — the
    // `spawn_controller` log task renders it as `Warning: …`.
    // afterLease hooks can be slow; match the beforeLease budget so a legitimately slow cleanup
    // hook isn't cut off (the lease is released either way once this returns).
    let deadline = Instant::now() + Duration::from_secs(300);
    while Instant::now() < deadline {
        let status =
            match tokio::time::timeout(RPC_TIMEOUT, client.get_status(GetStatusRequest {})).await {
                Ok(Ok(resp)) => ExporterStatus::try_from(resp.into_inner().status)
                    .unwrap_or(ExporterStatus::Unspecified),
                // Session/tunnel gone or RPC timed out — afterLease is done (or the
                // exporter exited); stop waiting.
                _ => {
                    debug!("end_session: status stream gone; afterLease assumed complete");
                    break;
                }
            };
        trace!(?status, "end_session: polled exporter status");
        match status {
            // afterLease ran but the hook failed: previously indistinguishable from
            // a clean completion — surface it so a failed afterLease is diagnosable.
            ExporterStatus::AfterLeaseHookFailed | ExporterStatus::BeforeLeaseHookFailed => {
                warn!(?status, "afterLease hook reported a terminal failed status");
                break;
            }
            // Clean: the exporter returned to Available/Offline (afterLease done).
            ExporterStatus::Available | ExporterStatus::Offline => {
                debug!(?status, "afterLease hook completed; exporter idle");
                break;
            }
            _ => {}
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    if Instant::now() >= deadline {
        warn!("afterLease wait timed out (300s); releasing lease anyway");
    }
}

/// Direct mode (`jmp shell --tls-grpc HOST:PORT`): connect a shell/command to a
/// standalone exporter with no controller/router/lease. Python does not proxy —
/// it hands `JUMPSTARTER_HOST=host:port` (+ `JMP_GRPC_INSECURE`/`JMP_GRPC_PASSPHRASE`)
/// to the child, whose `j`/driver client dials the exporter's `ExporterService`
/// directly (`shell.py:_shell_direct_async`, `DirectLease`). `drivers_allow` is the
/// literal `UNSAFE` (DirectLease uses `unsafe=True`).
pub async fn run_direct(
    address: &str,
    insecure: bool,
    passphrase: Option<&str>,
    exporter_logs: bool,
    command: &Option<Vec<String>>,
) -> Result<i32, ClientError> {
    let log_task = if exporter_logs {
        let task = crate::exporter_logs::spawn_direct(
            address.to_string(),
            insecure,
            passphrase.map(str::to_string),
        );
        // Let the stream connect and replay buffered (pre-connect) logs before the
        // command runs — mirrors Python's "stream logs, then run command" order.
        tokio::time::sleep(std::time::Duration::from_millis(500)).await;
        Some(task)
    } else {
        None
    };

    let result = spawn_child(
        command, address, address, "UNSAFE", insecure, passphrase, None,
    )
    .await;

    if let Some(task) = log_task {
        // Brief flush window for any trailing (afterLease) lines before aborting.
        tokio::time::sleep(std::time::Duration::from_millis(200)).await;
        task.abort();
    }
    result
}

/// The exporter-context env the shell exports for a remote lease (#53): `JMP_EXPORTER`,
/// `JMP_LEASE`, and `JMP_EXPORTER_LABELS` (a sorted, comma-joined `k=v` list). Code inside the shell
/// reads these via `jumpstarter.utils.env.env_with_metadata()` / `ExporterMetadata.from_env()`.
/// `None` in direct mode (no controller lease).
pub struct LeaseEnv {
    pub exporter: String,
    pub lease: String,
    pub labels: String,
}

/// Spawn the shell/command with the `JUMPSTARTER_HOST` env contract
/// (`common/utils.py:launch_shell`). `host` is the bare socket path (controller/
/// local mode) or `host:port` (direct mode); `context` labels the decorated prompt
/// (the exporter name); `passphrase` sets `JMP_GRPC_PASSPHRASE` for a standalone
/// exporter. `lease_env` exports the exporter-context vars for a remote lease (#53).
/// Reused by the CLI's direct and local-exporter shell paths.
pub async fn spawn_child(
    command: &Option<Vec<String>>,
    host: &str,
    context: &str,
    drivers_allow: &str,
    insecure: bool,
    passphrase: Option<&str>,
    lease_env: Option<&LeaseEnv>,
) -> Result<i32, ClientError> {
    let mut cmd = match command {
        Some(argv) if !argv.is_empty() => {
            let mut c = tokio::process::Command::new(&argv[0]);
            c.args(&argv[1..]);
            c
        }
        // An interactive shell gets a jumpstarter-decorated prompt (`{cwd} ⚡{context} ➤`).
        _ => decorated_shell(context),
    };

    cmd.env("JUMPSTARTER_HOST", host)
        .env("JMP_DRIVERS_ALLOW", drivers_allow)
        .env("_JMP_SUPPRESS_DRIVER_WARNINGS", "1");
    if insecure {
        cmd.env("JMP_GRPC_INSECURE", "1");
    }
    if let Some(p) = passphrase.filter(|p| !p.is_empty()) {
        cmd.env(jumpstarter_config::env::JMP_GRPC_PASSPHRASE, p);
    }
    if let Some(le) = lease_env {
        cmd.env("JMP_EXPORTER", &le.exporter);
        if !le.lease.is_empty() {
            cmd.env("JMP_LEASE", &le.lease);
        }
        if !le.labels.is_empty() {
            cmd.env("JMP_EXPORTER_LABELS", &le.labels);
        }
    }

    let status = cmd
        .status()
        .await
        .map_err(|e| ClientError::Config(format!("failed to spawn shell/command: {e}")))?;

    // Signal-terminated child -> 2 (matches `j`'s cancellation exit code).
    Ok(status.code().unwrap_or(2))
}

/// Build the interactive `$SHELL` command with a jumpstarter-decorated prompt —
/// gray cwd, yellow `⚡`, white exporter name, yellow `➤` — suppressing rc/profile
/// files so the prompt actually takes effect (`common/utils.py:launch_shell`).
/// bash/zsh/fish each get their native prompt syntax; any other shell just inherits
/// the env contract with its default prompt.
fn decorated_shell(context: &str) -> tokio::process::Command {
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/sh".to_string());
    let shell_name = std::path::Path::new(&shell)
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("sh")
        .to_string();
    let mut c = tokio::process::Command::new(&shell);
    if shell_name.ends_with("bash") {
        // `\[..\]` wraps non-printing escapes; `\W` is the basename of $PWD.
        c.arg("--norc").arg("--noprofile").env(
            "PS1",
            format!("\\[\\e[90m\\]\\W \\[\\e[93m\\]⚡\\[\\e[97m\\]{context} \\[\\e[93m\\]➤\\[\\e[0m\\] "),
        );
    } else if shell_name == "zsh" {
        c.arg("--no-rcs")
            .args(["-o", "inc_append_history", "-o", "share_history"])
            .env(
                "PS1",
                format!("%F{{8}}%1~ %F{{yellow}}⚡%F{{white}}{context} %F{{yellow}}➤%f "),
            );
        if std::env::var_os("HISTFILE").is_none() {
            if let Some(home) = std::env::var_os("HOME") {
                c.env("HISTFILE", std::path::Path::new(&home).join(".zsh_history"));
            }
        }
    } else if shell_name == "fish" {
        c.arg("--init-command").arg(format!(
            "function fish_prompt; set_color grey; printf \"%s\" (basename $PWD); \
             set_color yellow; printf \"⚡\"; set_color white; printf \"{context}\"; \
             set_color yellow; printf \"➤ \"; set_color normal; end"
        ));
    }
    c
}

/// Log the outcome of a best-effort lease release. The controller auto-releases a lease when its
/// session ends, so an explicit release after the command often races and returns
/// `FailedPrecondition: ... already been released` — that is the expected no-op, logged at debug.
/// Any other failure is a real warning.
fn log_release_outcome(name: &str, err: &impl std::fmt::Display) {
    let msg = err.to_string();
    if msg.contains("already") && msg.contains("released") {
        debug!(lease = %name, "lease already released by the controller (expected no-op)");
    } else {
        warn!(lease = %name, error = %msg, "failed to release lease");
    }
}
