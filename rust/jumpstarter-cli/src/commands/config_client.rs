//! `jmp config client {create,delete,list,use}` (spec 08 §11.1;
//! `config_client.py`). Purely local file operations — no controller access.

use std::path::PathBuf;

use clap::{Args as ClapArgs, Subcommand};
use jumpstarter_config::{paths, ClientConfig, DriversConfig, ObjectMeta, UserConfig, YamlConfig};
use serde::ser::{Serialize, SerializeMap, Serializer};

use crate::output::{self, ListFormat, PathFormat, Printable};
use crate::prompt;
use crate::userconfig;

#[derive(ClapArgs)]
pub struct Args {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Create a client config.
    Create(CreateArgs),
    /// Delete a client config.
    Delete(DeleteArgs),
    /// List available client configurations.
    List(ListArgs),
    /// Select the current client config.
    Use(UseArgs),
}

pub fn run(args: Args) -> u8 {
    let result = match args.command {
        Command::Create(a) => create(a),
        Command::Delete(a) => delete(a),
        Command::List(a) => list(a),
        Command::Use(a) => use_client(a),
    };
    match result {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("Error: {e}");
            1
        }
    }
}

// ---- create ---------------------------------------------------------------

#[derive(ClapArgs)]
struct CreateArgs {
    /// Client alias.
    alias: String,
    /// Specify an output file for the client config.
    #[arg(long)]
    out: Option<PathBuf>,
    #[arg(long)]
    namespace: Option<String>,
    #[arg(long)]
    name: Option<String>,
    #[arg(short = 'e', long)]
    endpoint: Option<String>,
    #[arg(short = 't', long)]
    token: Option<String>,
    #[arg(short = 'a', long)]
    allow: Option<String>,
    /// Allow all driver client packages to load (UNSAFE!).
    #[arg(long = "unsafe")]
    unsafe_drivers: bool,
    #[arg(short = 'o', long = "output", value_enum)]
    output: Option<PathFormat>,
}

fn create(a: CreateArgs) -> Result<(), String> {
    let namespace = prompt::value(a.namespace, "Enter a valid Jumpstarter client namespace")?;
    let name = prompt::value(a.name, "Enter a valid Jumpstarter client name")?;
    let endpoint = prompt::value(a.endpoint, "Enter a valid Jumpstarter service endpoint")?;
    let token = prompt::password(a.token, "Enter a Jumpstarter auth token (hidden)")?;
    let allow = match a.allow {
        Some(v) => v,
        None => prompt::default(
            "Enter a comma-separated list of allowed driver packages (optional)",
            "",
        )?,
    };

    if a.out.is_none() && paths::client_config_path(&a.alias).exists() {
        return Err(format!(
            "A client with the name '{}' already exists.",
            a.alias
        ));
    }

    let mut config = ClientConfig::new(ObjectMeta {
        namespace: Some(namespace),
        name,
    });
    config.endpoint = Some(endpoint);
    config.token = Some(token);
    let allow_list: Vec<String> = allow.split(',').map(String::from).collect();
    config.drivers = DriversConfig {
        // A literal `UNSAFE` entry forces unsafe mode (Python `decode_unsafe`).
        r#unsafe: a.unsafe_drivers || allow_list.iter().any(|d| d == "UNSAFE"),
        allow: allow_list,
    };

    let path = match &a.out {
        Some(out) => {
            config.save(out).map_err(|e| e.to_string())?;
            out.clone()
        }
        None => {
            let p = paths::client_config_path(&a.alias);
            config.save(&p).map_err(|e| e.to_string())?;
            p
        }
    };

    // If this is the only client config, set it as the default.
    if a.out.is_none() && userconfig::list_client_aliases().len() == 1 {
        let mut user = userconfig::load_or_create()?;
        user.config.current_client = Some(a.alias.clone());
        userconfig::save(&user)?;
    }

    if a.output.is_some() {
        println!("{}", path.display());
    }
    Ok(())
}

// ---- delete ---------------------------------------------------------------

#[derive(ClapArgs)]
struct DeleteArgs {
    /// Client alias to delete.
    name: String,
    #[arg(short = 'o', long = "output", value_enum)]
    output: Option<PathFormat>,
}

fn delete(a: DeleteArgs) -> Result<(), String> {
    set_next_client(&a.name)?;
    let path = paths::client_config_path(&a.name);
    if !path.exists() {
        return Err(format!(
            "Client config '{}' does not exist.",
            path.display()
        ));
    }
    std::fs::remove_file(&path).map_err(|e| e.to_string())?;
    if a.output.is_some() {
        println!("{}", path.display());
    }
    Ok(())
}

/// If the client being deleted is the current default, switch to the next
/// available client, or clear the selection (`config_client.py:set_next_client`).
fn set_next_client(name: &str) -> Result<(), String> {
    let user_path = paths::user_config_path();
    if !user_path.exists() {
        return Ok(());
    }
    let mut user = UserConfig::load(&user_path).map_err(|e| e.to_string())?;
    if user.current_client() == Some(name) {
        let next = userconfig::list_client_aliases()
            .into_iter()
            .find(|a| a != name);
        userconfig::use_client(&mut user, next.as_deref())?;
    }
    Ok(())
}

// ---- list -----------------------------------------------------------------

#[derive(ClapArgs)]
struct ListArgs {
    #[arg(short = 'o', long = "output", value_enum)]
    output: Option<ListFormat>,
}

fn list(a: ListArgs) -> Result<(), String> {
    let model = build_client_list();
    output::print(&model, ListFormat::resolve(a.output)).map_err(|e| e.to_string())
}

struct ClientItem {
    alias: String,
    path: PathBuf,
    config: ClientConfig,
}

struct ClientConfigList {
    current_config: Option<String>,
    items: Vec<ClientItem>,
}

fn build_client_list() -> ClientConfigList {
    let current_config = {
        let p = paths::user_config_path();
        p.exists()
            .then(|| UserConfig::load(&p).ok())
            .flatten()
            .and_then(|u| u.current_client().map(String::from))
            // Python resolves current-client by loading it; a dangling alias whose
            // file is gone reports `null`.
            .filter(|alias| paths::client_config_path(alias).exists())
    };
    let mut items = Vec::new();
    if let Ok(entries) = std::fs::read_dir(paths::client_configs_dir()) {
        for entry in entries.flatten() {
            let fname = entry.file_name();
            if !fname.to_string_lossy().ends_with(".yaml") {
                continue;
            }
            let path = entry.path();
            if let Ok(config) = ClientConfig::load(&path) {
                let alias = paths::alias_from_path(&path).unwrap_or_default();
                items.push(ClientItem {
                    alias,
                    path,
                    config,
                });
            }
        }
    }
    ClientConfigList {
        current_config,
        items,
    }
}

impl Printable for ClientConfigList {
    fn headers(&self) -> Vec<String> {
        ["CURRENT", "ALIAS", "ENDPOINT", "PATH"]
            .iter()
            .map(|s| s.to_string())
            .collect()
    }
    fn rows(&self) -> Vec<Vec<String>> {
        self.items
            .iter()
            .map(|c| {
                vec![
                    if self.current_config.as_deref() == Some(&c.alias) {
                        "*".to_string()
                    } else {
                        String::new()
                    },
                    c.alias.clone(),
                    c.config.endpoint.clone().unwrap_or_default(),
                    c.path.display().to_string(),
                ]
            })
            .collect()
    }
    fn names(&self) -> Vec<String> {
        self.items.iter().map(|c| c.alias.clone()).collect()
    }
}

/// JSON/YAML shape mirrors `ClientConfigListV1Alpha1` (`client.py:492`):
/// `apiVersion`, `currentConfig`, `items`, `kind`. Each item is a full
/// `model_dump` — runtime `alias`/`path`, an explicit `refresh_token: null`, and
/// the `leases` block are all present (the CLI's `model_print` applies no
/// exclusions). The `-o yaml` path sorts keys (see `output::to_yaml`).
impl Serialize for ClientConfigList {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut m = serializer.serialize_map(Some(4))?;
        m.serialize_entry("apiVersion", "jumpstarter.dev/v1alpha1")?;
        m.serialize_entry("currentConfig", &self.current_config)?;
        m.serialize_entry("items", &ClientItems(&self.items))?;
        m.serialize_entry("kind", "ClientConfigList")?;
        m.end()
    }
}

struct ClientItems<'a>(&'a [ClientItem]);
impl Serialize for ClientItems<'_> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use serde::ser::SerializeSeq;
        let mut seq = serializer.serialize_seq(Some(self.0.len()))?;
        for item in self.0 {
            seq.serialize_element(&ClientItemJson(item))?;
        }
        seq.end()
    }
}

struct ClientItemJson<'a>(&'a ClientItem);
impl Serialize for ClientItemJson<'_> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let c = &self.0.config;
        let mut m = serializer.serialize_map(None)?;
        m.serialize_entry("alias", &self.0.alias)?;
        m.serialize_entry("path", &self.0.path.display().to_string())?;
        m.serialize_entry("apiVersion", &c.api_version)?;
        m.serialize_entry("kind", &c.kind)?;
        m.serialize_entry("metadata", &MetaFull(&c.metadata))?;
        m.serialize_entry("endpoint", &c.endpoint)?;
        m.serialize_entry("tls", &c.tls)?;
        m.serialize_entry("token", &c.token)?;
        m.serialize_entry("refresh_token", &c.refresh_token)?;
        m.serialize_entry("grpcOptions", &c.grpc_options)?;
        m.serialize_entry("drivers", &c.drivers)?;
        m.serialize_entry("shell", &c.shell)?;
        m.serialize_entry("leases", &Leases(c.leases.acquisition_timeout))?;
        m.end()
    }
}

/// `metadata` with `namespace` always present (null when unset), matching a full
/// pydantic `model_dump`.
pub(crate) struct MetaFull<'a>(pub &'a jumpstarter_config::ObjectMeta);
impl Serialize for MetaFull<'_> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut m = serializer.serialize_map(Some(2))?;
        m.serialize_entry("namespace", &self.0.namespace)?;
        m.serialize_entry("name", &self.0.name)?;
        m.end()
    }
}

struct Leases(i64);
impl Serialize for Leases {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut m = serializer.serialize_map(Some(1))?;
        m.serialize_entry("acquisition_timeout", &self.0)?;
        m.end()
    }
}

// ---- use ------------------------------------------------------------------

#[derive(ClapArgs)]
struct UseArgs {
    /// Client alias to select.
    name: String,
    #[arg(short = 'o', long = "output", value_enum)]
    output: Option<PathFormat>,
}

fn use_client(a: UseArgs) -> Result<(), String> {
    let mut user = userconfig::load_or_create()?;
    let path = userconfig::use_client(&mut user, Some(&a.name))?;
    if a.output.is_some() {
        if let Some(p) = path {
            println!("{}", p.display());
        }
    }
    Ok(())
}
