//! `jmp get {exporters,leases}` (spec 08 §7.2-7.3; `get.py`).

use std::collections::HashMap;
use std::process::ExitCode;

use clap::{Args as ClapArgs, Subcommand, ValueEnum};
use jumpstarter_client::{selector_contains, ControllerClient};
use jumpstarter_protocol::client_v1;

use crate::clientcfg::ConfigOpts;
use crate::cmderr::{grpc, runtime, CmdError};
use crate::output::{self, ListFormat};
use crate::parsing::join_selector;
use crate::resources::{ExporterList, ExporterResource, LeaseList, LeaseResource, WithOptions};

#[derive(ClapArgs)]
pub struct Args {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Display one or many exporters.
    #[command(visible_alias = "exporter")]
    Exporters(ExportersArgs),
    /// Display one or many leases.
    #[command(visible_alias = "lease")]
    Leases(LeasesArgs),
}

pub async fn run(args: Args) -> ExitCode {
    let result = match args.command {
        Command::Exporters(a) => get_exporters(a).await,
        Command::Leases(a) => get_leases(a).await,
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => e.report(),
    }
}

#[derive(Clone, Copy, PartialEq, Eq, ValueEnum)]
#[value(rename_all = "lower")]
enum WithField {
    Leases,
    Online,
    Status,
}

#[derive(ClapArgs)]
struct ExportersArgs {
    #[command(flatten)]
    config: ConfigOpts,
    /// Selector (label query) to filter on; repeatable / comma-separated.
    #[arg(short = 'l', long = "selector")]
    selector: Vec<String>,
    /// Include fields: leases, online, status (comma-separated or repeated).
    #[arg(long = "with", value_enum, value_delimiter = ',')]
    with_fields: Vec<WithField>,
    #[arg(short = 'o', long = "output", value_enum)]
    output: Option<ListFormat>,
}

async fn get_exporters(a: ExportersArgs) -> Result<(), CmdError> {
    let config = a.config.resolve()?;
    let selector = join_selector(&a.selector);
    let options = WithOptions {
        show_online: a.with_fields.contains(&WithField::Online),
        show_status: a.with_fields.contains(&WithField::Status),
        show_leases: a.with_fields.contains(&WithField::Leases),
    };

    let controller = ControllerClient::connect(&config).await.map_err(grpc)?;
    let mut exporters: Vec<ExporterResource> = controller
        .list_exporters(selector.as_deref())
        .await
        .map_err(grpc)?
        .into_iter()
        .map(ExporterResource::from_proto)
        .collect();

    if options.show_leases {
        let leases = controller
            .list_leases(None, true, None)
            .await
            .map_err(grpc)?;
        let lease_map = active_lease_map(leases);
        for exporter in &mut exporters {
            if let Some(lease) = lease_map.get(&exporter.name) {
                exporter.lease = Some(lease.clone());
            }
        }
    }

    output::print(
        &ExporterList { exporters, options },
        ListFormat::resolve(a.output),
    )
    .map_err(runtime)
}

/// Map exporter name -> its active (Ready) lease (`config/client.py:220-228`).
fn active_lease_map(leases: Vec<client_v1::Lease>) -> HashMap<String, LeaseResource> {
    let mut map = HashMap::new();
    for lease in leases {
        let lease = LeaseResource::from_proto(lease);
        if lease.exporter.is_empty() || lease.effective_begin_time.is_none() {
            continue;
        }
        if let Some(c) = lease.conditions.last() {
            if c.r#type.as_deref() == Some("Ready") && c.status.as_deref() == Some("True") {
                map.insert(lease.exporter.clone(), lease);
            }
        }
    }
    map
}

#[derive(ClapArgs)]
struct LeasesArgs {
    #[command(flatten)]
    config: ConfigOpts,
    /// Selector (label query) to filter on; repeatable / comma-separated.
    #[arg(short = 'l', long = "selector")]
    selector: Vec<String>,
    /// Include expired leases.
    #[arg(short = 'a', long = "all")]
    show_all: bool,
    /// Include leases from all clients.
    #[arg(short = 'A', long = "all-clients")]
    all_clients: bool,
    /// Filter leases by tags (label selector syntax, e.g. build=1234).
    #[arg(long = "tag-filter")]
    tag_filter: Option<String>,
    #[arg(short = 'o', long = "output", value_enum)]
    output: Option<ListFormat>,
}

async fn get_leases(a: LeasesArgs) -> Result<(), CmdError> {
    let config = a.config.resolve()?;
    let selector = join_selector(&a.selector);

    let controller = ControllerClient::connect(&config).await.map_err(grpc)?;
    let mut leases: Vec<LeaseResource> = controller
        .list_leases(selector.as_deref(), !a.show_all, a.tag_filter.as_deref())
        .await
        .map_err(grpc)?
        .into_iter()
        .map(LeaseResource::from_proto)
        .collect();

    // Client-side matchExpression filtering (server only handles matchLabels).
    if let Some(sel) = &selector {
        leases.retain(|l| selector_contains(&l.selector, sel));
    }
    if !a.all_clients {
        let me = config.metadata.name.as_str();
        leases.retain(|l| l.client == me);
    }

    output::print(&LeaseList { leases }, ListFormat::resolve(a.output)).map_err(runtime)
}
