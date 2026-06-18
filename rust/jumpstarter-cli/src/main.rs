//! The Jumpstarter `jmp` CLI (Rust core) — the client-side command tree (spec 08).
//! The `jmp-admin` and `jmp-driver` sub-CLIs and the `j` driver dispatcher stay as
//! Python today (driver business logic), so they are not part of this binary.

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

use std::process::ExitCode;

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
    /// Get the current Jumpstarter version.
    Version(commands::version::Args),
    /// Generate a shell completion script.
    Completion(commands::completion::Args),
}

#[tokio::main]
async fn main() -> ExitCode {
    init_tracing();
    // Disambiguate the rustls crypto provider (multiple deps pull different ones).
    let _ = rustls::crypto::ring::default_provider().install_default();
    let cli = Cli::parse();
    match cli.command {
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
        Command::Version(args) => commands::version::run(args),
        Command::Completion(args) => commands::completion::run::<Cli>(args),
    }
}

fn init_tracing() {
    use tracing_subscriber::EnvFilter;
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .with_writer(std::io::stderr)
        .init();
}
