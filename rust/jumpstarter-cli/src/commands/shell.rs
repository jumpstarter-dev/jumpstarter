//! `jmp shell` — acquire (or reuse) a lease and run a shell/command wired to an
//! exporter (spec 08 §9.1-9.2; `shell.py`). The client-mode lease flow lives in
//! `jumpstarter-client`. Local-exporter (`--exporter-config`) and direct
//! (`--tls-grpc`) modes are not yet ported.

use std::io::IsTerminal;
use std::process::ExitCode;
use std::time::Duration;

use clap::Args as ClapArgs;
use jumpstarter_client::shell::{self, ShellOptions};
use jumpstarter_client::ControllerClient;
use jumpstarter_config::ClientConfig;

use crate::clientcfg::ConfigOpts;
use crate::cmderr::{self, CmdError};
use crate::parsing::parse_duration;
use crate::resources::LeaseResource;

#[derive(ClapArgs)]
pub struct Args {
    #[command(flatten)]
    config: ConfigOpts,
    /// Reuse an existing lease by name instead of creating one.
    #[arg(long = "lease", env = "JMP_LEASE")]
    lease: Option<String>,
    /// Label selector for the exporter (e.g. `board=rk3588`).
    #[arg(short = 'l', long)]
    selector: Option<String>,
    /// Target a specific exporter/device name directly.
    #[arg(short = 'n', long = "name")]
    exporter_name: Option<String>,
    /// Lease duration (e.g. 30m, 1h, 1d, or seconds as an integer).
    #[arg(long, value_parser = parse_duration, default_value = "30m")]
    duration: Duration,
    /// Override the lease acquisition timeout (must be >= 5 seconds).
    #[arg(long = "acquisition-timeout", value_parser = parse_acquisition_timeout)]
    acquisition_timeout: Option<Duration>,
    /// Connect directly to a standalone exporter at HOST:PORT (no controller).
    #[arg(long = "tls-grpc", value_name = "HOST:PORT")]
    tls_grpc: Option<String>,
    /// With --tls-grpc, connect without TLS verification (development only).
    #[arg(long = "tls-grpc-insecure")]
    tls_grpc_insecure: bool,
    /// Passphrase for authenticating with a standalone exporter (--tls-grpc).
    #[arg(long)]
    passphrase: Option<String>,
    /// Stream the exporter's logs (driver + hook output) during the session.
    #[arg(long = "exporter-logs")]
    exporter_logs: bool,
    /// Command to run instead of an interactive shell. Accepted both with a
    /// `--` separator (`jmp shell -- j power on`) and without it
    /// (`jmp shell --selector x j power on`), matching the Python CLI.
    #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
    command: Vec<String>,
}

pub async fn run(args: Args) -> ExitCode {
    match shell_impl(args).await {
        Ok(code) => ExitCode::from(u8::try_from(code).unwrap_or(1)),
        Err(e) => e.report(),
    }
}

async fn shell_impl(args: Args) -> Result<i32, CmdError> {
    // Direct mode bypasses the controller/config entirely (`shell.py` runs
    // `_shell_direct_async` before resolving config).
    if let Some(address) = &args.tls_grpc {
        let command = (!args.command.is_empty()).then_some(args.command.clone());
        return shell::run_direct(
            address,
            args.tls_grpc_insecure,
            args.passphrase.as_deref(),
            args.exporter_logs,
            &command,
        )
        .await
        .map_err(cmderr::grpc);
    }

    let config = args.config.resolve()?;

    // With no selector/name/lease, reconnect to the client's single active lease
    // (or prompt/error when there are several) — `shell.py:_resolve_lease`.
    let mut lease = args.lease;
    if lease.is_none() && args.selector.is_none() && args.exporter_name.is_none() {
        lease = Some(resolve_active_lease(&config).await?);
    }

    let opts = ShellOptions {
        selector: args.selector,
        exporter_name: args.exporter_name,
        lease_name: lease,
        duration: args.duration,
        acquisition_timeout: args.acquisition_timeout,
        exporter_logs: args.exporter_logs,
        command: (!args.command.is_empty()).then_some(args.command),
    };

    shell::run(&config, opts).await.map_err(cmderr::grpc)
}

/// `--acquisition-timeout` parses like a duration but must be >= 5 s
/// (`common.py:ACQUISITION_TIMEOUT`).
fn parse_acquisition_timeout(value: &str) -> Result<Duration, String> {
    let d = parse_duration(value)?;
    if d < Duration::from_secs(5) {
        return Err(format!("'{value}' must be at least 5 seconds"));
    }
    Ok(d)
}

/// Resolve the client's active lease when none was specified
/// (`shell.py:_resolve_lease_from_active_async`).
async fn resolve_active_lease(config: &ClientConfig) -> Result<String, CmdError> {
    let controller = ControllerClient::connect(config)
        .await
        .map_err(cmderr::grpc)?;
    let me = config.metadata.name.as_str();
    let leases: Vec<LeaseResource> = controller
        .list_leases(None, true, None)
        .await
        .map_err(cmderr::grpc)?
        .into_iter()
        .map(LeaseResource::from_proto)
        .filter(|l| l.client == me)
        .collect();

    match leases.as_slice() {
        [] => Err(CmdError::Usage(
            "no active leases found. Use --selector/-l or --name/-n to create one, \
             or create a lease with 'jmp create lease'."
                .to_string(),
        )),
        [only] => Ok(only.name.clone()),
        many if std::io::stdin().is_terminal() => {
            println!("Multiple active leases found:\n");
            for (i, l) in many.iter().enumerate() {
                println!("  {}) {}", i + 1, l.name);
                let info = format_lease_display(l);
                if !info.is_empty() {
                    println!("     {info}");
                }
            }
            println!();
            let choice = prompt_index(many.len())?;
            Ok(many[choice].name.clone())
        }
        many => {
            let summaries: Vec<String> = many
                .iter()
                .map(|l| {
                    let info = format_lease_display(l);
                    if info.is_empty() {
                        l.name.clone()
                    } else {
                        format!("{} ({})", l.name, info)
                    }
                })
                .collect();
            Err(CmdError::Usage(format!(
                "multiple active leases found:\n  {}\nUse --lease to specify one, \
                 or run interactively to select.",
                summaries.join("\n  ")
            )))
        }
    }
}

fn format_lease_display(lease: &LeaseResource) -> String {
    let mut parts = Vec::new();
    if !lease.exporter.is_empty() {
        parts.push(format!("exporter={}", lease.exporter));
    }
    if !lease.selector.is_empty() {
        parts.push(format!("selector={}", lease.selector));
    }
    let expires = lease
        .effective_end_time
        .or_else(|| lease.effective_begin_time.map(|b| b + lease.duration));
    if let Some(end) = expires {
        parts.push(format!("expires {}", end.format("%Y-%m-%d %H:%M")));
    }
    parts.join(", ")
}

/// Prompt for a 1-based index in `[1, n]`, returning the 0-based choice.
fn prompt_index(n: usize) -> Result<usize, CmdError> {
    use std::io::Write;
    loop {
        print!("Select a lease [1-{n}]: ");
        let _ = std::io::stdout().flush();
        let mut line = String::new();
        std::io::stdin()
            .read_line(&mut line)
            .map_err(|e| CmdError::Runtime(e.to_string()))?;
        if let Ok(i) = line.trim().parse::<usize>() {
            if (1..=n).contains(&i) {
                return Ok(i - 1);
            }
        }
        eprintln!("Error: invalid index, expected 1-{n}");
    }
}
