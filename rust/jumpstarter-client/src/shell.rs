//! `jmp shell` orchestration: acquire a lease, serve the transport host, run a
//! shell/command wired to it, and release the lease on exit
//! (`common/utils.py:launch_shell`, `jumpstarter-cli/.../shell.py`).
//!
//! The child inherits stdio (so an interactive shell works) and the established
//! environment contract: `JUMPSTARTER_HOST`, `JMP_DRIVERS_ALLOW`
//! (`"UNSAFE"` or the comma-joined allow-list), and `_JMP_SUPPRESS_DRIVER_WARNINGS`.

use std::time::Duration;

use jumpstarter_config::ClientConfig;

use crate::error::ClientError;
use crate::lease::{acquire, CreateLeaseParams, LeaseProvider, LeaseTiming};
use crate::service::ControllerClient;
use crate::transport;

/// Options for [`run`].
#[derive(Debug, Clone)]
pub struct ShellOptions {
    /// Label selector for the lease.
    pub selector: Option<String>,
    /// Specific exporter name (instead of a selector).
    pub exporter_name: Option<String>,
    /// Lease duration.
    pub duration: Duration,
    /// Command to run instead of an interactive `$SHELL`.
    pub command: Option<Vec<String>>,
}

impl Default for ShellOptions {
    fn default() -> Self {
        Self {
            selector: None,
            exporter_name: None,
            duration: Duration::from_secs(30 * 60),
            command: None,
        }
    }
}

/// Acquire a lease for `config`, serve the transport socket, run the shell/command,
/// release the lease, and return the child's exit code.
pub async fn run(config: &ClientConfig, opts: ShellOptions) -> Result<i32, ClientError> {
    let client = ControllerClient::connect(config).await?;

    let acquired = acquire(
        &client,
        CreateLeaseParams {
            selector: opts.selector.clone(),
            exporter_name: opts.exporter_name.clone(),
            duration: opts.duration,
            ..Default::default()
        },
        None,
        Some(&config.metadata.name),
        LeaseTiming::default(),
    )
    .await?;

    let host =
        transport::serve_default(client.clone(), acquired.name.clone(), config.tls.clone()).await?;

    let drivers_allow = if config.drivers.r#unsafe {
        "UNSAFE".to_string()
    } else {
        config.drivers.allow.join(",")
    };
    let insecure = crate::channel::is_insecure(&config.tls);

    let result = spawn_child(
        &opts.command,
        &host.jumpstarter_host(),
        &drivers_allow,
        insecure,
    )
    .await;

    // Release the lease regardless of how the child exited.
    let _ = client.delete_lease(&acquired.name).await;
    drop(host);

    result
}

async fn spawn_child(
    command: &Option<Vec<String>>,
    host: &str,
    drivers_allow: &str,
    insecure: bool,
) -> Result<i32, ClientError> {
    let mut cmd = match command {
        Some(argv) if !argv.is_empty() => {
            let mut c = tokio::process::Command::new(&argv[0]);
            c.args(&argv[1..]);
            c
        }
        _ => {
            let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/sh".to_string());
            tokio::process::Command::new(shell)
        }
    };

    cmd.env("JUMPSTARTER_HOST", host)
        .env("JMP_DRIVERS_ALLOW", drivers_allow)
        .env("_JMP_SUPPRESS_DRIVER_WARNINGS", "1");
    if insecure {
        cmd.env("JMP_GRPC_INSECURE", "1");
    }

    let status = cmd
        .status()
        .await
        .map_err(|e| ClientError::Config(format!("failed to spawn shell/command: {e}")))?;

    // Signal-terminated child -> 2 (matches `j`'s cancellation exit code).
    Ok(status.code().unwrap_or(2))
}
