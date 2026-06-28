//! `jmp create lease` (spec 08 §7.1; `create.py`).

use std::time::Duration;

use clap::{Args as ClapArgs, Subcommand};
use jumpstarter_lease::lease::CreateLeaseParams;
use jumpstarter_lease::ControllerClient;

use crate::clientcfg::ConfigOpts;
use crate::cmderr::{grpc, runtime, CmdError};
use crate::output::{self, ListFormat};
use crate::parsing::{join_selector, parse_datetime, parse_duration, parse_tags};
use crate::resources::LeaseResource;

#[derive(ClapArgs)]
pub struct Args {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Create a lease.
    #[command(visible_alias = "leases")]
    Lease(LeaseArgs),
}

pub async fn run(args: Args) -> u8 {
    let result = match args.command {
        Command::Lease(a) => create_lease(a).await,
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
    /// Selector (label query) to filter on; repeatable / comma-separated.
    #[arg(short = 'l', long = "selector")]
    selector: Vec<String>,
    /// Target a specific exporter/device name directly.
    #[arg(short = 'n', long = "name")]
    exporter_name: Option<String>,
    /// Duration of the lease (e.g. 30m, 1h, 1d, PT1H30M).
    #[arg(long, value_parser = parse_duration)]
    duration: Duration,
    /// Begin time for the lease (ISO 8601); defaults to immediately.
    #[arg(long = "begin-time", value_parser = parse_datetime)]
    begin_time: Option<prost_types::Timestamp>,
    /// Optional lease ID to request (server generates one if omitted).
    #[arg(long = "lease-id")]
    lease_id: Option<String>,
    /// Tag to set on the lease (key=value); repeatable.
    #[arg(long = "tag")]
    tags: Vec<String>,
    #[arg(short = 'o', long = "output", value_enum)]
    output: Option<ListFormat>,
}

async fn create_lease(a: LeaseArgs) -> Result<(), CmdError> {
    let config = a.config.resolve()?;

    let selector = join_selector(&a.selector);
    if selector.is_none() && a.exporter_name.is_none() {
        return Err(CmdError::Usage(
            "one of --selector/-l or --name/-n is required".to_string(),
        ));
    }
    let tags = parse_tags(&a.tags).map_err(CmdError::Usage)?;

    let controller = ControllerClient::connect(&config).await.map_err(grpc)?;
    let params = CreateLeaseParams {
        selector,
        exporter_name: a.exporter_name,
        duration: a.duration,
        begin_time: a.begin_time,
        lease_id: a.lease_id,
        tags,
    };
    let lease = controller.create_lease_raw(&params).await.map_err(grpc)?;
    output::print(
        &LeaseResource::from_proto(lease),
        ListFormat::resolve(a.output),
    )
    .map_err(runtime)
}
