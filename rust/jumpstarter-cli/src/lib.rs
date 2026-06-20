//! The Jumpstarter `jmp` CLI (Rust core) as a library, so the command tree can be driven
//! both by the `jmp` binary (`main.rs`) and by the language bindings via FFI
//! (`jumpstarter-core-uniffi::run_cli`). The `run` (driver host) and `j` (driver-client)
//! commands stay in each language's entrypoint and reach the core through the foreign-trait
//! seam; everything else is pure Rust dispatched here.

mod clientcfg;
mod cmderr;
mod commands;
mod jwt;
mod oidc;
mod output;
mod parsing;
mod prompt;
mod resources;
mod userconfig;

use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(name = "jmp", version, about = "Jumpstarter CLI (Rust core)")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Manage Jumpstarter Kubernetes objects (admin).
    Admin(commands::admin::Args),
    /// Acquire a lease and open a shell (or run a command) connected to an exporter.
    Shell(commands::shell::Args),
    /// Run an exporter locally.
    Run(commands::run::Args),
    /// Modify jumpstarter config files.
    Config(commands::config::Args),
    /// Create a resource.
    Create(commands::create::Args),
    /// Display one or many resources.
    Get(commands::get::Args),
    /// Delete resources.
    Delete(commands::delete::Args),
    /// Update a resource.
    Update(commands::update::Args),
    /// Authentication and token management commands.
    Auth(commands::auth::Args),
    /// Login into a Jumpstarter instance.
    Login(commands::login::Args),
    /// MCP server for AI agent interaction with Jumpstarter hardware.
    Mcp(commands::mcp::Args),
    /// Get the current Jumpstarter version.
    Version(commands::version::Args),
    /// Generate a shell completion script.
    Completion(commands::completion::Args),
}

/// Install the process-wide rustls crypto provider (multiple deps pull different ones).
/// Idempotent; safe to call from both the binary and the FFI entrypoint.
pub fn install_crypto_provider() {
    let _ = rustls::crypto::ring::default_provider().install_default();
}

/// Initialize tracing to stderr (honoring RUST_LOG / default `info`). Idempotent-ish:
/// a second init is ignored by the subscriber.
pub fn init_tracing() {
    use tracing_subscriber::EnvFilter;
    let _ = tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .with_writer(std::io::stderr)
        .try_init();
}

async fn run_command(command: Command) -> u8 {
    match command {
        Command::Admin(args) => commands::admin::run(args).await,
        Command::Shell(args) => commands::shell::run(args).await,
        Command::Run(args) => commands::run::run(args).await,
        Command::Config(args) => commands::config::run(args),
        Command::Create(args) => commands::create::run(args).await,
        Command::Get(args) => commands::get::run(args).await,
        Command::Delete(args) => commands::delete::run(args).await,
        Command::Update(args) => commands::update::run(args).await,
        Command::Auth(args) => commands::auth::run(args).await,
        Command::Login(args) => commands::login::run(args).await,
        Command::Mcp(args) => commands::mcp::run(args).await,
        Command::Version(args) => commands::version::run(args),
        Command::Completion(args) => commands::completion::run::<Cli>(args),
    }
}

/// Run the `jmp` CLI from an explicit argv (the binary passes `std::env::args`, the FFI
/// binding passes the forwarded Python argv). `args[0]` is the program name. Returns a
/// process exit code as a `u8` (rather than calling `std::process::exit`) so it is safe to
/// invoke from a hosted runtime (an embedded language binding must not kill its host).
///
/// `--help`/`--version` (which clap reports as "errors") print to stdout and yield 0; other
/// parse errors print to stderr and yield 2 — matching clap's own binary behavior.
pub async fn dispatch(args: Vec<String>) -> u8 {
    install_crypto_provider();
    let cli = match Cli::try_parse_from(args) {
        Ok(cli) => cli,
        Err(err) => {
            // clap renders help/version as an Err with a non-failure kind; print to stdout
            // and exit 0. Genuine usage errors print to stderr and exit 2.
            let _ = err.print();
            return match err.kind() {
                clap::error::ErrorKind::DisplayHelp
                | clap::error::ErrorKind::DisplayVersion
                | clap::error::ErrorKind::DisplayHelpOnMissingArgumentOrSubcommand => 0,
                _ => 2,
            };
        }
    };
    run_command(cli.command).await
}
