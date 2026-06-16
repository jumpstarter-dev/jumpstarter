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
mod tls;
mod user;

pub use client::{ClientConfig, DriversConfig, LeaseConfig, ShellConfig};
pub use env::{client_from_env, client_from_env_with};
pub use exporter::{
    DriverInstance, DriverInstanceBase, DriverInstanceComposite, DriverInstanceProxy,
    ExporterConfig, FailureDetectionConfig, HookConfig, HookInstanceConfig, OnFailure,
};
pub use grpc_options::{GrpcOptionValue, GrpcOptions};
pub use meta::ObjectMeta;
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

    /// Serialize and write a config file with `0o600` permissions (unix).
    fn save(&self, path: impl AsRef<Path>) -> Result<(), ConfigError> {
        let path = path.as_ref();
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(path, self.to_yaml()?)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
        }
        Ok(())
    }
}

impl<T: Serialize + DeserializeOwned + Sized> YamlConfig for T {}
