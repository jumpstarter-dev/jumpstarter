//! `jmp update lease` (spec 08 §7.5; `update.py`).

use std::time::Duration;

use clap::{Args as ClapArgs, Subcommand};
use jumpstarter_client::ControllerClient;

use crate::clientcfg::ConfigOpts;
use crate::cmderr::{grpc, runtime, CmdError};
use crate::output::{self, ListFormat};
use crate::parsing::{parse_datetime, parse_duration};
use crate::resources::LeaseResource;

#[derive(ClapArgs)]
pub struct Args {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Update a lease.
    #[command(visible_alias = "leases")]
    Lease(LeaseArgs),
}

pub async fn run(args: Args) -> u8 {
    let result = match args.command {
        Command::Lease(a) => update_lease(a).await,
    };
    match result {
        Ok(()) => 0,
        Err(e) => e.report(),
    }
}

#[derive(ClapArgs)]
struct LeaseArgs {
    #[command(flatten)]
    config: ConfigOpts,
    /// Lease name to update.
    name: String,
    /// New duration (e.g. 30m, 1h, 1d, PT1H30M).
    #[arg(long, value_parser = parse_duration)]
    duration: Option<Duration>,
    /// New begin time (ISO 8601).
    #[arg(long = "begin-time", value_parser = parse_datetime)]
    begin_time: Option<prost_types::Timestamp>,
    /// Transfer lease to a different client in the same namespace.
    #[arg(long = "to-client")]
    to_client: Option<String>,
    #[arg(short = 'o', long = "output", value_enum)]
    output: Option<ListFormat>,
}

async fn update_lease(a: LeaseArgs) -> Result<(), CmdError> {
    let config = a.config.resolve()?;

    if a.duration.is_none() && a.begin_time.is_none() && a.to_client.is_none() {
        return Err(CmdError::Usage(
            "At least one of --duration, --begin-time, or --to-client must be specified"
                .to_string(),
        ));
    }

    let client_path = a.to_client.as_ref().map(|c| {
        format!(
            "namespaces/{}/clients/{}",
            config.metadata.namespace.clone().unwrap_or_default(),
            c
        )
    });

    let controller = ControllerClient::connect(&config).await.map_err(grpc)?;
    let lease = controller
        .update_lease(&a.name, a.duration, a.begin_time, client_path)
        .await
        .map_err(grpc)?;
    output::print(
        &LeaseResource::from_proto(lease),
        ListFormat::resolve(a.output),
    )
    .map_err(runtime)
}
