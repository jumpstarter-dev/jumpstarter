//! `ClientConfigV1Alpha1` and its sub-blocks
//! (`python/packages/jumpstarter/jumpstarter/config/client.py`).
//!
//! Serialization mirrors Python `ClientConfigV1Alpha1.save`
//! (`client.py:392-428`): `None` options are omitted (`exclude_none=True`), and the
//! `leases` block is dropped entirely when it holds only defaults (a v0.7.x
//! backward-compat measure, `client.py:404-408`). Field order matches the Python
//! model so the YAML reads the same top to bottom.

use serde::{Deserialize, Serialize};

use crate::grpc_options::GrpcOptions;
use crate::meta::ObjectMeta;
use crate::tls::TlsConfig;

fn api_version() -> String {
    "jumpstarter.dev/v1alpha1".to_string()
}
fn kind() -> String {
    "ClientConfig".to_string()
}

/// Driver-client load policy (`client.py:72-91`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct DriversConfig {
    /// `fnmatch` allow-list of importable driver-client classes.
    #[serde(default)]
    pub allow: Vec<String>,
    /// Bypass the allow-list entirely. Also implied by `"UNSAFE"` appearing in
    /// `allow` (handled when building from the environment).
    #[serde(default, rename = "unsafe")]
    pub r#unsafe: bool,
}

/// Lease tuning (`client.py:94-107`). `dial_timeout` is an internal field that is
/// never written to (Python `exclude=True`); it is neither serialized nor read.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LeaseConfig {
    #[serde(default = "default_acquisition_timeout")]
    pub acquisition_timeout: i64,
    #[serde(skip, default = "default_dial_timeout")]
    pub dial_timeout: f64,
}

fn default_acquisition_timeout() -> i64 {
    7200
}
fn default_dial_timeout() -> f64 {
    30.0
}

impl Default for LeaseConfig {
    fn default() -> Self {
        Self {
            acquisition_timeout: default_acquisition_timeout(),
            dial_timeout: default_dial_timeout(),
        }
    }
}

impl LeaseConfig {
    /// Whether this block is all-defaults — used to drop it on save for backward
    /// compatibility with clients that predate the field.
    fn is_default(&self) -> bool {
        self.acquisition_timeout == default_acquisition_timeout()
    }
}

/// Shell behaviour (`config/shell.py`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct ShellConfig {
    #[serde(default)]
    pub use_profiles: bool,
}

/// A Jumpstarter client configuration (`jumpstarter.dev/v1alpha1 ClientConfig`).
///
/// `alias` and `path` are runtime-only (derived from the file name/location) and
/// are never part of the YAML.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ClientConfig {
    #[serde(rename = "apiVersion", default = "api_version")]
    pub api_version: String,
    #[serde(default = "kind")]
    pub kind: String,
    pub metadata: ObjectMeta,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub endpoint: Option<String>,
    #[serde(default)]
    pub tls: TlsConfig,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub token: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub refresh_token: Option<String>,
    #[serde(rename = "grpcOptions", default)]
    pub grpc_options: GrpcOptions,
    #[serde(default)]
    pub drivers: DriversConfig,
    #[serde(default)]
    pub shell: ShellConfig,
    #[serde(default, skip_serializing_if = "LeaseConfig::is_default")]
    pub leases: LeaseConfig,
}

impl ClientConfig {
    /// Construct a minimal config with the required identity + endpoint/token.
    pub fn new(metadata: ObjectMeta) -> Self {
        Self {
            api_version: api_version(),
            kind: kind(),
            metadata,
            endpoint: None,
            tls: TlsConfig::default(),
            token: None,
            refresh_token: None,
            grpc_options: GrpcOptions::new(),
            drivers: DriversConfig::default(),
            shell: ShellConfig::default(),
            leases: LeaseConfig::default(),
        }
    }
}
