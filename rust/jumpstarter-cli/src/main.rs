//! The Jumpstarter `jmp` CLI (Rust). Implements `jmp shell` (acquire a lease and
//! run a shell/command wired to an exporter) and `jmp run` (serve an exporter).

use std::process::ExitCode;
use std::time::Duration;

use clap::{Args, Parser, Subcommand};
use jumpstarter_client::shell::{self, ShellOptions};
use jumpstarter_config::{paths, ClientConfig, ExporterConfig, UserConfig, YamlConfig};
use jumpstarter_exporter::RunOptions;

#[derive(Parser)]
#[command(name = "jmp", version, about = "Jumpstarter CLI (Rust core)")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Acquire a lease and open a shell (or run a command) connected to an exporter.
    Shell(ShellArgs),
    /// Serve an exporter (register with the controller and host its drivers).
    Run(RunArgs),
}

#[derive(Args)]
struct RunArgs {
    /// Exporter config alias (resolved from the user dir, then /etc/jumpstarter/exporters).
    #[arg(long)]
    exporter: String,
}

#[derive(Args)]
struct ShellArgs {
    /// Client config alias (defaults to the current client from the user config).
    #[arg(long)]
    client: Option<String>,
    /// Label selector for the exporter (e.g. `board=rk3588`).
    #[arg(long, short)]
    selector: Option<String>,
    /// Specific exporter name (instead of a selector).
    #[arg(long)]
    exporter: Option<String>,
    /// Lease duration, in minutes.
    #[arg(long, default_value_t = 30)]
    duration: u64,
    /// Command to run instead of an interactive shell (after `--`).
    #[arg(last = true)]
    command: Vec<String>,
}

#[tokio::main]
async fn main() -> ExitCode {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .with_writer(std::io::stderr)
        .init();

    let cli = Cli::parse();
    match cli.command {
        Command::Shell(args) => run_shell(args).await,
        Command::Run(args) => run_exporter(args).await,
    }
}

async fn run_exporter(args: RunArgs) -> ExitCode {
    let path = paths::resolve_exporter_path(&args.exporter);
    let config = match ExporterConfig::load(&path) {
        Ok(config) => config,
        Err(e) => {
            eprintln!(
                "jmp: cannot load exporter '{}' ({}): {e}",
                args.exporter,
                path.display()
            );
            return ExitCode::from(1);
        }
    };
    match jumpstarter_exporter::run(RunOptions {
        config,
        config_path: path,
    })
    .await
    {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("jmp: {e}");
            ExitCode::from(1)
        }
    }
}

async fn run_shell(args: ShellArgs) -> ExitCode {
    let config = match resolve_client(args.client) {
        Ok(config) => config,
        Err(e) => {
            eprintln!("jmp: {e}");
            return ExitCode::from(1);
        }
    };

    let opts = ShellOptions {
        selector: args.selector,
        exporter_name: args.exporter,
        duration: Duration::from_secs(args.duration * 60),
        command: (!args.command.is_empty()).then_some(args.command),
    };

    match shell::run(&config, opts).await {
        Ok(code) => ExitCode::from(u8::try_from(code).unwrap_or(1)),
        Err(e) => {
            eprintln!("jmp: {e}");
            ExitCode::from(1)
        }
    }
}

/// Resolve a client config: an explicit `--client` alias, otherwise the
/// `current-client` from the user config (`config_home/config.yaml`).
fn resolve_client(alias: Option<String>) -> Result<ClientConfig, String> {
    let alias = match alias {
        Some(alias) => alias,
        None => {
            let user = UserConfig::load(paths::user_config_path())
                .map_err(|e| format!("no --client given and cannot read user config: {e}"))?;
            user.current_client()
                .ok_or("no --client given and no current client is set")?
                .to_string()
        }
    };
    let path = paths::client_config_path(&alias);
    ClientConfig::load(&path).map_err(|e| format!("cannot load client '{alias}': {e}"))
}
