//! Pure-data models for the three Jumpstarter YAML config kinds — `ClientConfig`,
//! `ExporterConfig`, and `UserConfig` — plus path resolution and the client
//! env-override builder.
//!
//! This crate deliberately depends on neither tonic nor the runtime crates
//! (spec `09-rust-core-requirements.md` §3.5): config parsing stays byte-for-byte
//! testable against Python-written fixtures with no I/O. The channel-construction
//! decision tree (doc 07 §8) is *consumed* in the runtime crates, not here.
//!
//! Serialization is idiomatic rather than byte-identical to Python's `yaml.safe_dump`
//! output: absent (`None`) options are omitted, map keys are sorted, and `null`
//! placeholders Python emits for unset optionals are dropped. These differences are
//! semantics-preserving and covered by round-trip + parse-compat tests in
//! `tests/roundtrip.rs`.

mod client;
pub mod env;
mod exporter;
mod grpc_options;
mod meta;
pub mod paths;
mod registry;
mod tls;
mod user;

pub use client::{ClientConfig, DriversConfig, LeaseConfig, ShellConfig};
pub use env::{client_from_env, client_from_env_with};
pub use exporter::{
    DriverInstance, DriverInstanceBase, DriverInstanceComposite, DriverInstanceProxy,
    ExporterConfig, FailureDetectionConfig, HookConfig, HookInstanceConfig, HostSpec, OnFailure,
};
pub use grpc_options::{GrpcOptionValue, GrpcOptions};
pub use meta::ObjectMeta;
pub use registry::{DriverRegistry, DriverRegistryEntry, InterfaceRegistryEntry};
pub use tls::TlsConfig;
pub use user::{UserConfig, UserConfigInner};

use std::path::Path;

use serde::{de::DeserializeOwned, Serialize};

/// Errors from loading/saving config files.
#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    #[error("config i/o error: {0}")]
    Io(#[from] std::io::Error),
    #[error("config yaml error: {0}")]
    Yaml(#[from] serde_yaml_ng::Error),
}

/// YAML load/save convenience for any config model. Blanket-implemented for every
/// serde type in the crate, so `ClientConfig::from_yaml`, `ExporterConfig::load`,
/// etc. are all available.
pub trait YamlConfig: Serialize + DeserializeOwned + Sized {
    /// Parse from a YAML string.
    fn from_yaml(s: &str) -> Result<Self, ConfigError> {
        Ok(serde_yaml_ng::from_str(s)?)
    }

    /// Serialize to a YAML string.
    fn to_yaml(&self) -> Result<String, ConfigError> {
        Ok(serde_yaml_ng::to_string(self)?)
    }

    /// Read and parse a config file.
    fn load(path: impl AsRef<Path>) -> Result<Self, ConfigError> {
        let raw = std::fs::read_to_string(path)?;
        Self::from_yaml(&raw)
    }

    /// Serialize and write a config file **atomically** with `0o600` permissions (unix).
    ///
    /// The config file holds bearer tokens, so it must never be observed partially written or
    /// briefly world-readable: write to a temp file in the *same* directory (so the rename is
    /// atomic on one filesystem), created `0o600` from the start, flush + `fsync`, then rename
    /// onto the destination. On any failure the temp file is cleaned up.
    fn save(&self, path: impl AsRef<Path>) -> Result<(), ConfigError> {
        use std::io::Write as _;

        let path = path.as_ref();
        let parent = match path.parent() {
            Some(p) if !p.as_os_str().is_empty() => p,
            _ => Path::new("."),
        };
        std::fs::create_dir_all(parent)?;
        let yaml = self.to_yaml()?;

        let file_name = path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("config");
        let tmp = parent.join(format!(".{file_name}.tmp.{}", std::process::id()));

        let write_result = (|| -> std::io::Result<()> {
            let mut opts = std::fs::OpenOptions::new();
            opts.write(true).create(true).truncate(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                opts.mode(0o600);
            }
            let mut f = opts.open(&tmp)?;
            f.write_all(yaml.as_bytes())?;
            f.flush()?;
            f.sync_all()?;
            drop(f);
            #[cfg(unix)]
            {
                // Re-assert 0o600 even if the temp pre-existed with looser perms.
                use std::os::unix::fs::PermissionsExt;
                std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600))?;
            }
            std::fs::rename(&tmp, path)
        })();

        if write_result.is_err() {
            let _ = std::fs::remove_file(&tmp); // best-effort cleanup
        }
        write_result?;
        Ok(())
    }
}

impl<T: Serialize + DeserializeOwned + Sized> YamlConfig for T {}
