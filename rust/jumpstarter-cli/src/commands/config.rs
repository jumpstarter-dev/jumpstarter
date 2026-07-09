//! `jmp config` — local configuration commands (spec 08 §11). Groups the
//! `client` and `exporter` subgroups.

use clap::{Args as ClapArgs, Subcommand};

use crate::commands::{config_client, config_exporter};

#[derive(ClapArgs)]
pub struct Args {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Modify jumpstarter client config files.
    Client(config_client::Args),
    /// Modify jumpstarter exporter config files.
    Exporter(config_exporter::Args),
}

pub fn run(args: Args) -> u8 {
    match args.command {
        Command::Client(a) => config_client::run(a),
        Command::Exporter(a) => config_exporter::run(a),
    }
}
