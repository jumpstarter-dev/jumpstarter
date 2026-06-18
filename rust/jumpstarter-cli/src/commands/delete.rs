//! `jmp delete leases` (spec 08 §7.4; `delete.py`).

use std::process::ExitCode;

use clap::{Args as ClapArgs, Subcommand, ValueEnum};
use jumpstarter_client::lease::LeaseProvider;
use jumpstarter_client::{selector_contains, ControllerClient};

use crate::clientcfg::ConfigOpts;
use crate::cmderr::{grpc, CmdError};
use crate::parsing::join_selector;
use crate::resources::LeaseResource;

#[derive(ClapArgs)]
pub struct Args {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Delete leases.
    #[command(visible_alias = "lease")]
    Leases(LeasesArgs),
}

pub async fn run(args: Args) -> ExitCode {
    let result = match args.command {
        Command::Leases(a) => delete_leases(a).await,
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => e.report(),
    }
}

#[derive(Clone, Copy, PartialEq, Eq, ValueEnum)]
#[value(rename_all = "lower")]
enum NameOutput {
    Name,
}

#[derive(ClapArgs)]
struct LeasesArgs {
    #[command(flatten)]
    config: ConfigOpts,
    /// Lease names to delete.
    names: Vec<String>,
    /// Selector (label query) to filter on; repeatable / comma-separated.
    #[arg(short = 'l', long = "selector")]
    selector: Vec<String>,
    /// Delete all your active leases.
    #[arg(short = 'a', long = "all")]
    delete_all: bool,
    /// Delete active leases from all clients.
    #[arg(short = 'A', long = "all-clients")]
    all_clients: bool,
    #[arg(short = 'o', long = "output", value_enum)]
    output: Option<NameOutput>,
}

async fn delete_leases(a: LeasesArgs) -> Result<(), CmdError> {
    let config = a.config.resolve()?;
    let selector = join_selector(&a.selector);

    // The no-criteria error is raised before connecting (Python connects lazily).
    if a.names.is_empty() && selector.is_none() && !a.delete_all && !a.all_clients {
        return Err(CmdError::Runtime(
            "One of NAMES, --selector, --all or --all-clients must be specified".to_string(),
        ));
    }

    let controller = ControllerClient::connect(&config).await.map_err(grpc)?;
    let me = config.metadata.name.as_str();

    let to_delete: Vec<String> = if !a.names.is_empty() {
        a.names.clone()
    } else if let Some(sel) = &selector {
        let mut leases: Vec<LeaseResource> = controller
            .list_leases(Some(sel), true, None)
            .await
            .map_err(grpc)?
            .into_iter()
            .map(LeaseResource::from_proto)
            .collect();
        leases.retain(|l| selector_contains(&l.selector, sel));
        if !a.all_clients {
            leases.retain(|l| l.client == me);
        }
        leases.into_iter().map(|l| l.name).collect()
    } else if a.delete_all || a.all_clients {
        let mut leases: Vec<LeaseResource> = controller
            .list_leases(None, true, None)
            .await
            .map_err(grpc)?
            .into_iter()
            .map(LeaseResource::from_proto)
            .collect();
        if !a.all_clients {
            leases.retain(|l| l.client == me);
        }
        leases.into_iter().map(|l| l.name).collect()
    } else {
        return Err(CmdError::Runtime(
            "One of NAMES, --selector, --all or --all-clients must be specified".to_string(),
        ));
    };

    if to_delete.is_empty() {
        return Err(CmdError::Runtime(
            "no leases found matching the criteria".to_string(),
        ));
    }

    for name in to_delete {
        controller.delete_lease(&name).await.map_err(grpc)?;
        match a.output {
            Some(NameOutput::Name) => println!("{name}"),
            None => println!("lease \"{name}\" deleted"),
        }
    }
    Ok(())
}
