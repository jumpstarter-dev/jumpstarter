//! The Jumpstarter `jmp` CLI (Rust). Currently implements `jmp shell` — the
//! capstone of the transport host: acquire a lease, serve `JUMPSTARTER_HOST`, and
//! run a shell/command wired to the exporter.

use std::process::ExitCode;
use std::time::Duration;

use clap::{Args, Parser, Subcommand};
use jumpstarter_client::shell::{self, ShellOptions};
use jumpstarter_config::{paths, ClientConfig, UserConfig, YamlConfig};

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
    let cli = Cli::parse();
    match cli.command {
        Command::Shell(args) => run_shell(args).await,
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
