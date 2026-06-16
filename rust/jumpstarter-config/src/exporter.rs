//! `ExporterConfigV1Alpha1` and its sub-blocks
//! (`python/packages/jumpstarter/jumpstarter/config/exporter.py`).
//!
//! The driver tree (`export`) is a recursive map of [`DriverInstance`] nodes, each
//! of which is one of three shapes distinguished structurally: a concrete driver
//! (`type:`), a composite (only `children:`), or a proxy (`ref:`) — mirroring the
//! Python `RootModel` union (`exporter.py:105-110`).

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::grpc_options::GrpcOptions;
use crate::meta::ObjectMeta;
use crate::tls::TlsConfig;

fn api_version() -> String {
    "jumpstarter.dev/v1alpha1".to_string()
}
fn kind() -> String {
    "ExporterConfig".to_string()
}

/// What to do when a lifecycle hook fails (`exporter.py:42-53`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
pub enum OnFailure {
    /// Continue and print a warning.
    #[default]
    #[serde(rename = "warn")]
    Warn,
    /// End the current lease.
    #[serde(rename = "endLease")]
    EndLease,
    /// Take the exporter offline and end the lease.
    #[serde(rename = "exit")]
    Exit,
}

fn default_hook_timeout() -> i64 {
    120
}

/// One lifecycle hook (`exporter.py:26-53`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HookInstanceConfig {
    /// Interpreter override (e.g. `/bin/bash`, `python3`); auto-detected when absent.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub exec: Option<String>,
    /// The `j` script to run.
    pub script: String,
    #[serde(default = "default_hook_timeout")]
    pub timeout: i64,
    #[serde(rename = "onFailure", default)]
    pub on_failure: OnFailure,
}

/// `beforeLease` / `afterLease` hooks (`exporter.py:56-62`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct HookConfig {
    #[serde(
        rename = "beforeLease",
        default,
        skip_serializing_if = "Option::is_none"
    )]
    pub before_lease: Option<HookInstanceConfig>,
    #[serde(
        rename = "afterLease",
        default,
        skip_serializing_if = "Option::is_none"
    )]
    pub after_lease: Option<HookInstanceConfig>,
}

fn default_max_rapid_failures() -> i64 {
    5
}
fn default_rapid_failure_window() -> i64 {
    60
}

/// Rapid-failure circuit breaker for the supervisor (`exporter.py:65-86`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FailureDetectionConfig {
    #[serde(rename = "maxRapidFailures", default = "default_max_rapid_failures")]
    pub max_rapid_failures: i64,
    #[serde(
        rename = "rapidFailureWindow",
        default = "default_rapid_failure_window"
    )]
    pub rapid_failure_window: i64,
}

impl Default for FailureDetectionConfig {
    fn default() -> Self {
        Self {
            max_rapid_failures: default_max_rapid_failures(),
            rapid_failure_window: default_rapid_failure_window(),
        }
    }
}

/// A concrete driver instance (`exporter.py:97-103`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DriverInstanceBase {
    pub r#type: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(default)]
    pub methods_description: BTreeMap<String, String>,
    /// Driver kwargs — arbitrary YAML/JSON (`dict[str, Any]`).
    #[serde(default)]
    pub config: serde_json::Map<String, serde_json::Value>,
    #[serde(default)]
    pub children: BTreeMap<String, DriverInstance>,
}

/// A composite node that only groups children (`exporter.py:93-94`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct DriverInstanceComposite {
    #[serde(default)]
    pub children: BTreeMap<String, DriverInstance>,
}

/// A proxy/alias node referencing another instance by name (`exporter.py:89-90`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DriverInstanceProxy {
    #[serde(rename = "ref")]
    pub reference: String,
}

/// A node in the `export` driver tree. Resolved structurally, matching the Python
/// `RootModel` union: a `type:` makes it [`DriverInstance::Base`], a `ref:` makes
/// it [`DriverInstance::Proxy`], otherwise it is a [`DriverInstance::Composite`].
///
/// Variant order matters for the untagged deserializer: `Base` (requires `type`)
/// and `Proxy` (requires `ref`) are attempted before the catch-all `Composite`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum DriverInstance {
    Base(DriverInstanceBase),
    Proxy(DriverInstanceProxy),
    Composite(DriverInstanceComposite),
}

/// A Jumpstarter exporter configuration (`jumpstarter.dev/v1alpha1 ExporterConfig`).
///
/// `alias` and `path` are runtime-only and never part of the YAML.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ExporterConfig {
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
    #[serde(rename = "grpcOptions", default)]
    pub grpc_options: GrpcOptions,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(default)]
    pub export: BTreeMap<String, DriverInstance>,
    #[serde(default)]
    pub hooks: HookConfig,
    #[serde(rename = "failureDetection", default)]
    pub failure_detection: FailureDetectionConfig,
}

impl ExporterConfig {
    pub fn new(metadata: ObjectMeta) -> Self {
        Self {
            api_version: api_version(),
            kind: kind(),
            metadata,
            endpoint: None,
            tls: TlsConfig::default(),
            token: None,
            grpc_options: GrpcOptions::new(),
            description: None,
            export: BTreeMap::new(),
            hooks: HookConfig::default(),
            failure_detection: FailureDetectionConfig::default(),
        }
    }
}
