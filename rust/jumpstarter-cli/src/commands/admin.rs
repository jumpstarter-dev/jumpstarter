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
    /// Import configs from a Kubernetes cluster.
    Import(Import),
    /// Rotate credentials.
    Rotate(Rotate),
}

pub async fn run(args: Args) -> u8 {
    let result = match args.command {
        Command::Create(a) => a.run().await,
        Command::Delete(a) => a.run().await,
        Command::Get(a) => a.run().await,
        Command::Import(a) => a.run().await,
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

// ---- import ---------------------------------------------------------------

#[derive(ClapArgs)]
struct Import {
    #[command(subcommand)]
    command: ImportKind,
}

#[derive(Subcommand)]
enum ImportKind {
    Client(ImportArgs),
    Exporter(ImportArgs),
}

#[derive(ClapArgs)]
struct ImportArgs {
    #[arg(default_value = "default")]
    name: String,
    #[command(flatten)]
    cluster: ClusterOpts,
    #[arg(long)]
    out: Option<PathBuf>,
    #[arg(short = 'a', long)]
    allow: Option<String>,
    #[arg(long = "unsafe")]
    unsafe_drivers: bool,
    #[arg(short = 'k', long = "insecure-tls")]
    insecure_tls: bool,
    #[arg(long)]
    nointeractive: bool,
    #[arg(short = 'o', long = "output", value_enum)]
    output: Option<ListFormat>,
}

impl Import {
    async fn run(self) -> Result<(), CmdError> {
        let (kind, a) = match self.command {
            ImportKind::Client(a) => (Kind::Client, a),
            ImportKind::Exporter(a) => (Kind::Exporter, a),
        };
        let noun = kind_noun(kind);
        // Refuse to clobber an existing local config (unless writing to an explicit --out).
        if a.out.is_none() {
            let path = match kind {
                Kind::Client => paths::client_config_path(&a.name),
                Kind::Exporter => paths::exporter_user_path(&a.name),
                Kind::Lease => unreachable!(),
            };
            if path.exists() {
                return Err(CmdError::Usage(format!(
                    "a {noun} with the name '{}' already exists",
                    a.name
                )));
            }
        }

        let admin = a.cluster.connect().await?;
        if a.output.is_none() {
            println!("Fetching {noun} credentials from cluster");
        }
        let (endpoint, token) = admin.credentials(kind, &a.name).await.map_err(runtime)?;
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
        } else {
            println!("{}", path.display());
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
    /// Display the devices hosted by the exporter(s).
    #[arg(short = 'd', long)]
    devices: bool,
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
        let list = ResourceList { kind, items, devices: a.devices };
        output::print(&list, ListFormat::resolve(a.output)).map_err(runtime)
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
    /// `get exporter --devices`: one row per hosted device.
    devices: bool,
}

impl serde::Serialize for ResourceList {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        // Mirror Kubernetes-style list output of the raw objects.
        self.items.serialize(serializer)
    }
}

/// A JSON string field at `ptr` in `data`, or `""`.
fn jstr(data: &serde_json::Value, ptr: &str) -> String {
    data.pointer(ptr).and_then(|v| v.as_str()).unwrap_or("").to_string()
}

/// `k:v,k:v` rendering of a JSON-object label map, keys sorted.
fn join_labels(map: Option<&serde_json::Value>) -> String {
    map.and_then(|v| v.as_object())
        .map(|m| {
            let mut kv: Vec<String> =
                m.iter().map(|(k, v)| format!("{k}:{}", v.as_str().unwrap_or_default())).collect();
            kv.sort();
            kv.join(",")
        })
        .unwrap_or_default()
}

/// Humanize an elapsed duration in seconds (`datetime.time_since`).
fn age_from_secs(secs: i64) -> String {
    let secs = secs.max(0);
    if secs < 60 {
        format!("{secs}s")
    } else if secs < 3600 {
        let (m, s) = (secs / 60, secs % 60);
        if s > 0 { format!("{m}m{s}s") } else { format!("{m}m") }
    } else if secs < 86400 {
        let (h, m) = (secs / 3600, (secs % 3600) / 60);
        if m > 0 && h < 2 { format!("{h}h{m}m") } else { format!("{h}h") }
    } else if secs < 2_592_000 {
        let (d, h) = (secs / 86400, (secs % 86400) / 3600);
        if h > 0 { format!("{d}d{h}h") } else { format!("{d}d") }
    } else if secs < 31_536_000 {
        let days = secs / 86400;
        let (mo, d) = (days / 30, days % 30);
        if d > 0 { format!("{mo}mo{d}d") } else { format!("{mo}mo") }
    } else {
        let days = secs / 86400;
        let (y, mo) = (days / 365, (days % 365) / 30);
        if mo > 0 { format!("{y}y{mo}mo") } else { format!("{y}y") }
    }
}

/// Humanize the age since a Kubernetes `creationTimestamp`.
fn humanize_age(o: &DynamicObject) -> String {
    match o.metadata.creation_timestamp.as_ref().map(|t| t.0) {
        Some(created) => age_from_secs(chrono::Utc::now().signed_duration_since(created).num_seconds()),
        None => String::new(),
    }
}

fn exporter_status(data: &serde_json::Value) -> String {
    let s = jstr(data, "/status/exporterStatus");
    if s.is_empty() { "Unknown".to_string() } else { s }
}

fn client_row(name: &str, data: &serde_json::Value) -> Vec<String> {
    vec![name.to_string(), jstr(data, "/status/endpoint")]
}

fn exporter_rows(name: &str, data: &serde_json::Value, age: &str, devices: bool) -> Vec<Vec<String>> {
    let status = exporter_status(data);
    let endpoint = jstr(data, "/status/endpoint");
    let device_list = data.pointer("/status/devices").and_then(|v| v.as_array());
    if devices {
        device_list
            .map(|ds| {
                ds.iter()
                    .map(|d| {
                        let labels = join_labels(d.pointer("/labels"));
                        let uuid = d.pointer("/uuid").and_then(|v| v.as_str()).unwrap_or("").to_string();
                        vec![name.to_string(), status.clone(), endpoint.clone(), age.to_string(), labels, uuid]
                    })
                    .collect()
            })
            .unwrap_or_default()
    } else {
        let count = device_list.map(|a| a.len()).unwrap_or(0);
        vec![vec![name.to_string(), status, endpoint, count.to_string(), age.to_string()]]
    }
}

fn lease_row(name: &str, data: &serde_json::Value, age: &str) -> Vec<String> {
    let selectors = {
        let s = join_labels(data.pointer("/spec/selector/matchLabels"));
        if s.is_empty() { "*".to_string() } else { s }
    };
    let ended = data.pointer("/status/ended").and_then(|v| v.as_bool()).unwrap_or(false);
    let reason = data
        .pointer("/status/conditions")
        .and_then(|v| v.as_array())
        .and_then(|a| a.last())
        .map(|c| c.pointer("/reason").and_then(|v| v.as_str()).unwrap_or("Unknown").to_string())
        .unwrap_or_else(|| "Unknown".to_string());
    vec![
        name.to_string(),
        jstr(data, "/spec/client/name"),
        selectors,
        jstr(data, "/status/exporter/name"),
        jstr(data, "/spec/duration"),
        if ended { "Ended".to_string() } else { "InProgress".to_string() },
        reason,
        jstr(data, "/status/beginTime"),
        jstr(data, "/status/endTime"),
        age.to_string(),
    ]
}

impl ResourceList {
    /// The rows for one object (exporter `--devices` yields one row per device).
    fn rows_for(&self, o: &DynamicObject) -> Vec<Vec<String>> {
        let name = o.metadata.name.clone().unwrap_or_default();
        let age = humanize_age(o);
        match self.kind {
            Kind::Client => vec![client_row(&name, &o.data)],
            Kind::Exporter => exporter_rows(&name, &o.data, &age, self.devices),
            Kind::Lease => vec![lease_row(&name, &o.data, &age)],
        }
    }
}

impl Printable for ResourceList {
    fn headers(&self) -> Vec<String> {
        let h: &[&str] = match self.kind {
            Kind::Client => &["NAME", "ENDPOINT"],
            Kind::Exporter if self.devices => &["NAME", "STATUS", "ENDPOINT", "AGE", "LABELS", "UUID"],
            Kind::Exporter => &["NAME", "STATUS", "ENDPOINT", "DEVICES", "AGE"],
            Kind::Lease => &[
                "NAME", "CLIENT", "SELECTOR", "EXPORTER", "DURATION", "STATUS", "REASON", "BEGIN", "END", "AGE",
            ],
        };
        h.iter().map(|s| s.to_string()).collect()
    }
    fn rows(&self) -> Vec<Vec<String>> {
        self.items.iter().flat_map(|o| self.rows_for(o)).collect()
    }
    fn names(&self) -> Vec<String> {
        let noun = kind_noun(self.kind);
        self.items
            .iter()
            .filter_map(|o| o.metadata.name.as_ref())
            .map(|n| format!("{noun}.jumpstarter.dev/{n}"))
            .collect()
    }
}

fn print_object(obj: &DynamicObject, output: Option<ListFormat>) -> Result<(), CmdError> {
    match output {
        Some(_) => {
            let list = ResourceList {
                kind: Kind::Client,
                items: vec![obj.clone()],
                devices: false,
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

// Ported from the deleted Python kubernetes get_client_config/get_exporter_config
// tests (CA-bundle inclusion) + the admin create/import `--save` behavior: the
// config written from fetched credentials carries the endpoint, token, CA, and
// driver allow-list.
#[cfg(test)]
mod tests {
    use super::*;
    use jumpstarter_config::YamlConfig;

    #[test]
    fn save_config_writes_client_with_ca_and_drivers() {
        let dir = std::env::temp_dir().join(format!("jmp-admin-test-client-{}", std::process::id()));
        let path = dir.join("c1.yaml");
        let _ = std::fs::remove_file(&path);
        save_config(
            Kind::Client,
            "ns1",
            "c1",
            "grpc.example.com:1443",
            "tok-123",
            "ca-bundle-data",
            vec!["vendorpackage.*".to_string()],
            false,
            true,
            Some(&path),
        )
        .unwrap();

        let cfg = ClientConfig::load(&path).unwrap();
        assert_eq!(cfg.metadata.namespace.as_deref(), Some("ns1"));
        assert_eq!(cfg.metadata.name, "c1");
        assert_eq!(cfg.endpoint.as_deref(), Some("grpc.example.com:1443"));
        assert_eq!(cfg.token.as_deref(), Some("tok-123"));
        assert_eq!(cfg.tls.ca, "ca-bundle-data");
        assert!(cfg.tls.insecure);
        assert_eq!(cfg.drivers.allow, vec!["vendorpackage.*"]);
        assert!(!cfg.drivers.r#unsafe);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn save_config_unsafe_via_allow_sentinel() {
        let dir = std::env::temp_dir().join(format!("jmp-admin-test-unsafe-{}", std::process::id()));
        let path = dir.join("c2.yaml");
        let _ = std::fs::remove_file(&path);
        save_config(
            Kind::Client,
            "ns",
            "c2",
            "ep:1443",
            "t",
            "",
            vec!["UNSAFE".to_string()],
            false,
            false,
            Some(&path),
        )
        .unwrap();
        let cfg = ClientConfig::load(&path).unwrap();
        assert!(cfg.drivers.r#unsafe, "UNSAFE in the allow-list implies unsafe");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn save_config_writes_exporter_with_ca() {
        let dir = std::env::temp_dir().join(format!("jmp-admin-test-exporter-{}", std::process::id()));
        let path = dir.join("e1.yaml");
        let _ = std::fs::remove_file(&path);
        save_config(
            Kind::Exporter,
            "ns",
            "e1",
            "grpc.example.com:1443",
            "etok",
            "exporter-ca",
            Vec::new(),
            false,
            false,
            Some(&path),
        )
        .unwrap();
        let cfg = ExporterConfig::load(&path).unwrap();
        assert_eq!(cfg.metadata.name, "e1");
        assert_eq!(cfg.endpoint.as_deref(), Some("grpc.example.com:1443"));
        assert_eq!(cfg.token.as_deref(), Some("etok"));
        assert_eq!(cfg.tls.ca, "exporter-ca");
        let _ = std::fs::remove_dir_all(&dir);
    }

    // Ported from the deleted Python admin get tests (jumpstarter-kubernetes rich
    // table output): the typed columns for client/exporter/lease + `--devices`.
    #[test]
    fn age_matches_python_time_since() {
        assert_eq!(age_from_secs(30), "30s");
        assert_eq!(age_from_secs(60), "1m");
        assert_eq!(age_from_secs(90), "1m30s");
        assert_eq!(age_from_secs(3600), "1h");
        assert_eq!(age_from_secs(3660), "1h1m"); // h < 2 keeps the minutes
        assert_eq!(age_from_secs(7200), "2h"); // h >= 2 drops the minutes
        assert_eq!(age_from_secs(90_000), "1d1h");
        assert_eq!(age_from_secs(86_400), "1d");
    }

    #[test]
    fn client_row_is_name_and_endpoint() {
        let data = serde_json::json!({"status": {"endpoint": "grpc.example.com:1443"}});
        assert_eq!(client_row("c1", &data), vec!["c1", "grpc.example.com:1443"]);
        // No status → empty endpoint.
        assert_eq!(client_row("c2", &serde_json::json!({})), vec!["c2", ""]);
    }

    #[test]
    fn exporter_row_summary_and_devices() {
        let data = serde_json::json!({"status": {
            "exporterStatus": "Online",
            "endpoint": "grpc.example.com:1443",
            "devices": [
                {"uuid": "u1", "labels": {"jumpstarter.dev/name": "power"}},
                {"uuid": "u2", "labels": {}},
            ],
        }});
        // Summary row: NAME, STATUS, ENDPOINT, DEVICES(count), AGE.
        assert_eq!(
            exporter_rows("e1", &data, "5m", false),
            vec![vec!["e1", "Online", "grpc.example.com:1443", "2", "5m"]]
        );
        // --devices: one row per device with LABELS + UUID.
        let rows = exporter_rows("e1", &data, "5m", true);
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0], vec!["e1", "Online", "grpc.example.com:1443", "5m", "jumpstarter.dev/name:power", "u1"]);
        assert_eq!(rows[1], vec!["e1", "Online", "grpc.example.com:1443", "5m", "", "u2"]);
    }

    #[test]
    fn exporter_status_defaults_to_unknown() {
        assert_eq!(exporter_status(&serde_json::json!({"status": {}})), "Unknown");
        assert_eq!(exporter_rows("e", &serde_json::json!({}), "1s", false)[0][1], "Unknown");
    }

    #[test]
    fn lease_row_columns() {
        let data = serde_json::json!({
            "spec": {
                "client": {"name": "client-a"},
                "selector": {"matchLabels": {"board": "rpi"}},
                "duration": "30m0s",
            },
            "status": {
                "ended": true,
                "exporter": {"name": "exp-1"},
                "beginTime": "2026-01-01T00:00:00Z",
                "endTime": "2026-01-01T00:30:00Z",
                "conditions": [{"reason": "Released"}],
            },
        });
        assert_eq!(
            lease_row("l1", &data, "2d"),
            vec![
                "l1", "client-a", "board:rpi", "exp-1", "30m0s", "Ended", "Released",
                "2026-01-01T00:00:00Z", "2026-01-01T00:30:00Z", "2d",
            ]
        );
    }

    #[test]
    fn lease_row_defaults_empty_selector_to_star_and_in_progress() {
        let data = serde_json::json!({"spec": {}, "status": {}});
        let row = lease_row("l2", &data, "1m");
        assert_eq!(row[2], "*"); // empty selector
        assert_eq!(row[5], "InProgress"); // not ended
        assert_eq!(row[6], "Unknown"); // no conditions
    }
}
