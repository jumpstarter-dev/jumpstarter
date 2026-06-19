//! `jmp admin {create,delete,get,rotate}` — Kubernetes management of Jumpstarter
//! Client/Exporter/Lease resources (`jumpstarter-cli-admin`). Backed by
//! `jumpstarter-admin` (kube-rs). The `cluster` subcommands are not ported.

use std::collections::BTreeMap;
use std::path::PathBuf;

use clap::{Args as ClapArgs, Subcommand};
use jumpstarter_admin::{DynamicObject, JumpstarterAdmin, Kind};
use jumpstarter_config::{
    paths, ClientConfig, DriversConfig, ExporterConfig, ObjectMeta, YamlConfig,
};

use crate::cmderr::{runtime, CmdError};
use crate::output::{self, ListFormat, Printable};
use crate::userconfig;

#[derive(ClapArgs)]
pub struct Args {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Create Jumpstarter Kubernetes objects.
    Create(Create),
    /// Delete Jumpstarter Kubernetes objects.
    Delete(Delete),
    /// Display Jumpstarter Kubernetes objects.
    Get(Get),
    /// Rotate credentials.
    Rotate(Rotate),
}

pub async fn run(args: Args) -> u8 {
    let result = match args.command {
        Command::Create(a) => a.run().await,
        Command::Delete(a) => a.run().await,
        Command::Get(a) => a.run().await,
        Command::Rotate(a) => a.run().await,
    };
    match result {
        Ok(()) => 0,
        Err(e) => e.report(),
    }
}

/// Shared cluster-connection options.
#[derive(ClapArgs, Clone)]
struct ClusterOpts {
    #[arg(short = 'n', long, default_value = "default")]
    namespace: String,
    #[arg(long)]
    kubeconfig: Option<PathBuf>,
    #[arg(long)]
    context: Option<String>,
}

impl ClusterOpts {
    async fn connect(&self) -> Result<JumpstarterAdmin, CmdError> {
        JumpstarterAdmin::connect(
            self.namespace.clone(),
            self.kubeconfig
                .as_ref()
                .map(|p| p.to_string_lossy().into_owned())
                .as_deref(),
            self.context.as_deref(),
        )
        .await
        .map_err(runtime)
    }
}

fn parse_labels(values: &[String]) -> Result<BTreeMap<String, String>, CmdError> {
    let mut out = BTreeMap::new();
    for v in values {
        let (k, val) = v
            .split_once('=')
            .ok_or_else(|| CmdError::Usage(format!("invalid label '{v}' (expected key=value)")))?;
        out.insert(k.to_string(), val.to_string());
    }
    Ok(out)
}

// ---- create ---------------------------------------------------------------

#[derive(ClapArgs)]
struct Create {
    #[command(subcommand)]
    command: CreateKind,
}

#[derive(Subcommand)]
enum CreateKind {
    Client(CreateArgs),
    Exporter(CreateArgs),
}

#[derive(ClapArgs)]
struct CreateArgs {
    name: String,
    #[command(flatten)]
    cluster: ClusterOpts,
    #[arg(short = 'l', long = "label")]
    labels: Vec<String>,
    /// Save the config file for the created object.
    #[arg(short = 's', long)]
    save: bool,
    #[arg(long)]
    out: Option<PathBuf>,
    #[arg(short = 'a', long)]
    allow: Option<String>,
    #[arg(long = "unsafe")]
    unsafe_drivers: bool,
    #[arg(short = 'k', long = "insecure-tls")]
    insecure_tls: bool,
    #[arg(long = "oidc-username")]
    oidc_username: Option<String>,
    #[arg(long)]
    nointeractive: bool,
    #[arg(short = 'o', long = "output", value_enum)]
    output: Option<ListFormat>,
}

impl Create {
    async fn run(self) -> Result<(), CmdError> {
        let (kind, a) = match self.command {
            CreateKind::Client(a) => (Kind::Client, a),
            CreateKind::Exporter(a) => (Kind::Exporter, a),
        };
        let admin = a.cluster.connect().await?;
        let labels = parse_labels(&a.labels)?;
        let noun = kind_noun(kind);
        if a.output.is_none() {
            println!(
                "Creating {noun} '{}' in namespace '{}'",
                a.name, a.cluster.namespace
            );
        }
        let created = admin
            .create(kind, &a.name, labels, a.oidc_username.as_deref())
            .await
            .map_err(runtime)?;

        if a.save || a.out.is_some() {
            if a.output.is_none() {
                println!("Fetching {noun} credentials from cluster");
            }
            let (endpoint, token) = admin.credentials(kind, &a.name).await.map_err(runtime)?;
            // Embed the cluster CA bundle so the config works over secure TLS
            // (`clients.py:get_client_config` / `exporters.py:get_exporter_config`).
            let ca = admin.ca_bundle().await.map_err(runtime)?;
            let allow_list: Vec<String> = match &a.allow {
                Some(s) if !s.is_empty() => s.split(',').map(String::from).collect(),
                _ => Vec::new(),
            };
            let path = save_config(
                kind,
                &a.cluster.namespace,
                &a.name,
                &endpoint,
                &token,
                &ca,
                allow_list,
                a.unsafe_drivers,
                a.insecure_tls,
                a.out.as_deref(),
            )?;
            if a.output.is_none() {
                println!(
                    "{} configuration successfully saved to {}",
                    cap(noun),
                    path.display()
                );
            }
        }

        print_object(&created, a.output)
    }
}

#[allow(clippy::too_many_arguments)]
fn save_config(
    kind: Kind,
    namespace: &str,
    name: &str,
    endpoint: &str,
    token: &str,
    ca: &str,
    allow: Vec<String>,
    unsafe_drivers: bool,
    insecure_tls: bool,
    out: Option<&std::path::Path>,
) -> Result<PathBuf, CmdError> {
    let meta = ObjectMeta {
        namespace: Some(namespace.to_string()),
        name: name.to_string(),
    };
    match kind {
        Kind::Client => {
            let mut config = ClientConfig::new(meta);
            config.endpoint = Some(endpoint.to_string());
            config.token = Some(token.to_string());
            config.drivers = DriversConfig {
                r#unsafe: unsafe_drivers || allow.iter().any(|d| d == "UNSAFE"),
                allow,
            };
            config.tls.ca = ca.to_string();
            config.tls.insecure = insecure_tls;
            let path = out
                .map(PathBuf::from)
                .unwrap_or_else(|| paths::client_config_path(name));
            config.save(&path).map_err(runtime)?;
            // Auto-default if this is the only client config.
            if out.is_none() && userconfig::list_client_aliases().len() == 1 {
                let mut user = userconfig::load_or_create()?;
                user.config.current_client = Some(name.to_string());
                userconfig::save(&user)?;
            }
            Ok(path)
        }
        Kind::Exporter => {
            let mut config = ExporterConfig::new(meta);
            config.endpoint = Some(endpoint.to_string());
            config.token = Some(token.to_string());
            config.tls.ca = ca.to_string();
            config.tls.insecure = insecure_tls;
            let path = out
                .map(PathBuf::from)
                .unwrap_or_else(|| paths::exporter_user_path(name));
            config.save(&path).map_err(runtime)?;
            Ok(path)
        }
        Kind::Lease => Err(CmdError::Usage("cannot save a lease config".to_string())),
    }
}

// ---- delete ---------------------------------------------------------------

#[derive(ClapArgs)]
struct Delete {
    #[command(subcommand)]
    command: DeleteKind,
}

#[derive(Subcommand)]
enum DeleteKind {
    Client(DeleteArgs),
    Exporter(DeleteArgs),
}

#[derive(ClapArgs)]
struct DeleteArgs {
    name: String,
    #[command(flatten)]
    cluster: ClusterOpts,
    /// Also delete the local config.
    #[arg(long)]
    delete: bool,
    #[arg(long)]
    nointeractive: bool,
    #[arg(short = 'o', long = "output", value_enum)]
    output: Option<NameOutput>,
}

#[derive(Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
#[value(rename_all = "lower")]
enum NameOutput {
    Name,
}

impl Delete {
    async fn run(self) -> Result<(), CmdError> {
        let (kind, a) = match self.command {
            DeleteKind::Client(a) => (Kind::Client, a),
            DeleteKind::Exporter(a) => (Kind::Exporter, a),
        };
        let admin = a.cluster.connect().await?;
        admin.delete(kind, &a.name).await.map_err(runtime)?;
        let noun = kind_noun(kind);
        match a.output {
            Some(NameOutput::Name) => println!("{noun}.jumpstarter.dev/{}", a.name),
            None => println!(
                "Deleted {noun} '{}' in namespace '{}'",
                a.name, a.cluster.namespace
            ),
        }

        // Optionally remove the local config too.
        if a.delete {
            let path = match kind {
                Kind::Client => paths::client_config_path(&a.name),
                Kind::Exporter => paths::exporter_user_path(&a.name),
                Kind::Lease => return Ok(()),
            };
            if path.exists() {
                let _ = std::fs::remove_file(&path);
                if a.output.is_none() {
                    println!("{} configuration successfully deleted", cap(noun));
                }
            }
        }
        Ok(())
    }
}

// ---- get ------------------------------------------------------------------

#[derive(ClapArgs)]
struct Get {
    #[command(subcommand)]
    command: GetKind,
}

#[derive(Subcommand)]
enum GetKind {
    Client(GetArgs),
    Exporter(GetArgs),
    Lease(GetArgs),
}

#[derive(ClapArgs)]
struct GetArgs {
    name: Option<String>,
    #[command(flatten)]
    cluster: ClusterOpts,
    #[arg(short = 'o', long = "output", value_enum)]
    output: Option<ListFormat>,
}

impl Get {
    async fn run(self) -> Result<(), CmdError> {
        let (kind, a) = match self.command {
            GetKind::Client(a) => (Kind::Client, a),
            GetKind::Exporter(a) => (Kind::Exporter, a),
            GetKind::Lease(a) => (Kind::Lease, a),
        };
        let admin = a.cluster.connect().await?;
        let items = match &a.name {
            Some(name) => vec![admin.get(kind, name).await.map_err(runtime)?],
            None => admin.list(kind).await.map_err(runtime)?,
        };
        output::print(&ResourceList { kind, items }, ListFormat::resolve(a.output)).map_err(runtime)
    }
}

// ---- rotate ---------------------------------------------------------------

#[derive(ClapArgs)]
struct Rotate {
    #[command(subcommand)]
    command: RotateKind,
}

#[derive(Subcommand)]
enum RotateKind {
    Client(RotateArgs),
}

#[derive(ClapArgs)]
struct RotateArgs {
    name: String,
    #[command(flatten)]
    cluster: ClusterOpts,
    #[arg(short = 's', long)]
    save: bool,
    #[arg(long)]
    out: Option<PathBuf>,
    #[arg(short = 'o', long = "output", value_enum)]
    output: Option<ListFormat>,
}

impl Rotate {
    async fn run(self) -> Result<(), CmdError> {
        let RotateKind::Client(a) = self.command;
        let admin = a.cluster.connect().await?;
        if a.output.is_none() {
            println!(
                "Rotating token for client '{}' in namespace '{}'",
                a.name, a.cluster.namespace
            );
        }
        let new_token = admin.rotate(Kind::Client, &a.name).await.map_err(runtime)?;
        if a.output.is_none() {
            println!("Token rotated for client '{}'", a.name);
        }
        if a.save || a.out.is_some() {
            let path = a
                .out
                .clone()
                .unwrap_or_else(|| paths::client_config_path(&a.name));
            let mut config = ClientConfig::load(&path).map_err(runtime)?;
            config.token = Some(new_token);
            config.save(&path).map_err(runtime)?;
            if a.output.is_none() {
                println!("Client configuration updated with new token");
            }
        }
        Ok(())
    }
}

// ---- output ---------------------------------------------------------------

struct ResourceList {
    kind: Kind,
    items: Vec<DynamicObject>,
}

impl serde::Serialize for ResourceList {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        // Mirror Kubernetes-style list output of the raw objects.
        self.items.serialize(serializer)
    }
}

impl Printable for ResourceList {
    fn headers(&self) -> Vec<String> {
        ["NAME", "LABELS"].iter().map(|s| s.to_string()).collect()
    }
    fn rows(&self) -> Vec<Vec<String>> {
        self.items
            .iter()
            .map(|o| {
                let name = o.metadata.name.clone().unwrap_or_default();
                let labels = o
                    .metadata
                    .labels
                    .as_ref()
                    .map(|m| {
                        let mut kv: Vec<String> =
                            m.iter().map(|(k, v)| format!("{k}={v}")).collect();
                        kv.sort();
                        kv.join(",")
                    })
                    .unwrap_or_default();
                vec![name, labels]
            })
            .collect()
    }
    fn names(&self) -> Vec<String> {
        let _ = self.kind;
        self.items
            .iter()
            .filter_map(|o| o.metadata.name.clone())
            .collect()
    }
}

fn print_object(obj: &DynamicObject, output: Option<ListFormat>) -> Result<(), CmdError> {
    match output {
        Some(_) => {
            let list = ResourceList {
                kind: Kind::Client,
                items: vec![obj.clone()],
            };
            output::print(&list, ListFormat::resolve(output)).map_err(runtime)
        }
        None => Ok(()),
    }
}

fn kind_noun(kind: Kind) -> &'static str {
    match kind {
        Kind::Client => "client",
        Kind::Exporter => "exporter",
        Kind::Lease => "lease",
    }
}

fn cap(s: &str) -> String {
    let mut c = s.chars();
    match c.next() {
        Some(f) => f.to_uppercase().chain(c).collect(),
        None => String::new(),
    }
}
