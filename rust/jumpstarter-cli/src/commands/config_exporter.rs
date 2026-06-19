//! `jmp config exporter {create,delete,edit,list}` (spec 08 §11.2;
//! `config_exporter.py`). Local file operations; the user dir shadows the system
//! dir `/etc/jumpstarter/exporters`.

use std::path::PathBuf;

use clap::{Args as ClapArgs, Subcommand};
use jumpstarter_config::{paths, ExporterConfig, ObjectMeta, YamlConfig};
use serde::ser::{Serialize, SerializeMap, SerializeSeq, Serializer};

use crate::output::{self, ListFormat, PathFormat, Printable};
use crate::prompt;

#[derive(ClapArgs)]
pub struct Args {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Create an exporter config.
    Create(CreateArgs),
    /// Delete an exporter config.
    Delete(DeleteArgs),
    /// Edit an exporter config.
    Edit(EditArgs),
    /// List exporter configs.
    List(ListArgs),
}

pub fn run(args: Args) -> u8 {
    let result = match args.command {
        Command::Create(a) => create(a),
        Command::Delete(a) => delete(a),
        Command::Edit(a) => edit(a),
        Command::List(a) => list(a),
    };
    match result {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("Error: {e}");
            1
        }
    }
}

/// Validate an alias the way `ExporterConfigV1Alpha1.validate_alias` does; an
/// invalid value is a clap usage error (exit 2, matching click's `BadParameter`).
fn parse_alias(s: &str) -> Result<String, String> {
    if s.is_empty() || s == "." || s == ".." || s.contains('/') || s.contains('\\') {
        return Err(format!(
            "Invalid exporter alias '{s}': must not contain path separators or be '.' / '..'"
        ));
    }
    Ok(s.to_string())
}

// ---- create ---------------------------------------------------------------

#[derive(ClapArgs)]
struct CreateArgs {
    /// Exporter alias.
    #[arg(default_value = "default", value_parser = parse_alias)]
    alias: String,
    #[arg(long)]
    namespace: Option<String>,
    #[arg(long)]
    name: Option<String>,
    #[arg(long)]
    endpoint: Option<String>,
    #[arg(long)]
    token: Option<String>,
    #[arg(short = 'o', long = "output", value_enum)]
    output: Option<PathFormat>,
}

fn create(a: CreateArgs) -> Result<(), String> {
    // click's `prompt=True` derives the prompt text from the option name.
    let namespace = prompt::value(a.namespace, "Namespace")?;
    let name = prompt::value(a.name, "Name")?;
    let endpoint = prompt::value(a.endpoint, "Endpoint")?;
    let token = prompt::value(a.token, "Token")?;

    // Refuse to overwrite a user-level config (a shadowed system config is fine).
    if paths::exporter_user_path(&a.alias).exists() {
        return Err(format!("exporter \"{}\" exists", a.alias));
    }

    let mut config = ExporterConfig::new(ObjectMeta {
        namespace: Some(namespace),
        name,
    });
    config.endpoint = Some(endpoint);
    config.token = Some(token);

    let path = paths::exporter_user_path(&a.alias);
    config.save(&path).map_err(|e| e.to_string())?;

    if a.output.is_some() {
        println!("{}", path.display());
    }
    Ok(())
}

// ---- delete ---------------------------------------------------------------

#[derive(ClapArgs)]
struct DeleteArgs {
    #[arg(default_value = "default", value_parser = parse_alias)]
    alias: String,
    #[arg(short = 'o', long = "output", value_enum)]
    output: Option<PathFormat>,
}

fn delete(a: DeleteArgs) -> Result<(), String> {
    // Must exist somewhere (user or system) to be deletable.
    let resolved = paths::resolve_exporter_path(&a.alias);
    if !resolved.exists() {
        return Err(format!("exporter \"{}\" does not exist", a.alias));
    }
    let user_path = paths::exporter_user_path(&a.alias);
    let system_path = paths::exporter_system_path(&a.alias);
    if !user_path.exists() {
        // Only a system config exists — not deletable.
        return Err(format!(
            "Exporter config '{}' exists only in the system location '{}' and cannot be deleted.",
            a.alias,
            system_path.display()
        ));
    }
    std::fs::remove_file(&user_path).map_err(|e| e.to_string())?;

    if a.output.is_some() {
        println!("{}", user_path.display());
    }
    if system_path.exists() {
        eprintln!(
            "Warning: {} deleted, but a system config at {} still exists and will now be used.",
            user_path.display(),
            system_path.display()
        );
    }
    Ok(())
}

// ---- edit -----------------------------------------------------------------

#[derive(ClapArgs)]
struct EditArgs {
    #[arg(default_value = "default", value_parser = parse_alias)]
    alias: String,
}

fn edit(a: EditArgs) -> Result<(), String> {
    let path = paths::resolve_exporter_path(&a.alias);
    if !path.exists() {
        return Err(format!("exporter \"{}\" does not exist", a.alias));
    }
    let editor = std::env::var("VISUAL")
        .or_else(|_| std::env::var("EDITOR"))
        .unwrap_or_else(|_| "vi".to_string());
    let status = std::process::Command::new(&editor)
        .arg(&path)
        .status()
        .map_err(|e| format!("failed to launch editor '{editor}': {e}"))?;
    if !status.success() {
        return Err(format!("editor '{editor}' exited with {status}"));
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
    let model = build_exporter_list();
    output::print(&model, ListFormat::resolve(a.output)).map_err(|e| e.to_string())
}

struct ExporterItem {
    alias: String,
    path: PathBuf,
    config: ExporterConfig,
}

struct ExporterConfigList {
    items: Vec<ExporterItem>,
}

/// Merge user-dir and system-dir exporter configs, user aliases taking precedence
/// (`exporter.py:list`).
fn build_exporter_list() -> ExporterConfigList {
    let mut aliases: Vec<String> = Vec::new();
    for dir in [
        paths::exporter_user_dir(),
        PathBuf::from(paths::EXPORTER_SYSTEM_DIR),
    ] {
        if let Ok(entries) = std::fs::read_dir(&dir) {
            for entry in entries.flatten() {
                let fname = entry.file_name();
                let fname = fname.to_string_lossy();
                if fname.ends_with(".yaml") {
                    if let Some(alias) =
                        paths::alias_from_path(std::path::Path::new(fname.as_ref()))
                    {
                        if !aliases.contains(&alias) {
                            aliases.push(alias);
                        }
                    }
                }
            }
        }
    }
    let mut items = Vec::new();
    for alias in aliases {
        let path = paths::resolve_exporter_path(&alias);
        if let Ok(config) = ExporterConfig::load(&path) {
            items.push(ExporterItem {
                alias,
                path,
                config,
            });
        }
    }
    ExporterConfigList { items }
}

impl Printable for ExporterConfigList {
    fn headers(&self) -> Vec<String> {
        ["ALIAS", "PATH"].iter().map(|s| s.to_string()).collect()
    }
    fn rows(&self) -> Vec<Vec<String>> {
        self.items
            .iter()
            .map(|e| vec![e.alias.clone(), e.path.display().to_string()])
            .collect()
    }
    fn names(&self) -> Vec<String> {
        self.items.iter().map(|e| e.alias.clone()).collect()
    }
}

/// JSON/YAML shape mirrors `ExporterConfigListV1Alpha1`: `apiVersion`, `items`,
/// `kind`. Each item is a full `model_dump` — runtime `alias` (first) and `path`
/// (last), plus explicit `description: null` and `hooks: {beforeLease: null,
/// afterLease: null}` when unset (the CLI applies no exclusions). `-o yaml` sorts.
impl Serialize for ExporterConfigList {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        struct Items<'a>(&'a [ExporterItem]);
        impl Serialize for Items<'_> {
            fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
                let mut seq = serializer.serialize_seq(Some(self.0.len()))?;
                for item in self.0 {
                    seq.serialize_element(&ExporterItemJson(item))?;
                }
                seq.end()
            }
        }
        let mut m = serializer.serialize_map(Some(3))?;
        m.serialize_entry("apiVersion", "jumpstarter.dev/v1alpha1")?;
        m.serialize_entry("items", &Items(&self.items))?;
        m.serialize_entry("kind", "ExporterConfigList")?;
        m.end()
    }
}

struct ExporterItemJson<'a>(&'a ExporterItem);
impl Serialize for ExporterItemJson<'_> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use crate::commands::config_client::MetaFull;
        let c = &self.0.config;
        let mut m = serializer.serialize_map(None)?;
        m.serialize_entry("alias", &self.0.alias)?;
        m.serialize_entry("apiVersion", &c.api_version)?;
        m.serialize_entry("kind", &c.kind)?;
        m.serialize_entry("metadata", &MetaFull(&c.metadata))?;
        m.serialize_entry("endpoint", &c.endpoint)?;
        m.serialize_entry("tls", &c.tls)?;
        m.serialize_entry("token", &c.token)?;
        m.serialize_entry("grpcOptions", &c.grpc_options)?;
        m.serialize_entry("description", &c.description)?;
        m.serialize_entry("export", &c.export)?;
        m.serialize_entry("hooks", &HooksFull(&c.hooks))?;
        m.serialize_entry("failureDetection", &c.failure_detection)?;
        m.serialize_entry("path", &self.0.path.display().to_string())?;
        m.end()
    }
}

/// `hooks` with both keys always present (null when unset), matching a full
/// pydantic `model_dump`.
struct HooksFull<'a>(&'a jumpstarter_config::HookConfig);
impl Serialize for HooksFull<'_> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut m = serializer.serialize_map(Some(2))?;
        m.serialize_entry("beforeLease", &self.0.before_lease)?;
        m.serialize_entry("afterLease", &self.0.after_lease)?;
        m.end()
    }
}
