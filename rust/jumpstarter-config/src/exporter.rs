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

// Ported from the deleted Python `config/exporter_test.py`: the `export` driver
// tree (the untagged Base/Composite union), `beforeLease`/`afterLease` hooks, and
// round-trip.
#[cfg(test)]
mod tests {
    use super::*;
    use crate::YamlConfig;

    const EXPORTER_YAML: &str = "apiVersion: jumpstarter.dev/v1alpha1\n\
kind: ExporterConfig\n\
metadata:\n  namespace: default\n  name: test\n\
endpoint: jumpstarter.my-lab.com:1443\n\
token: a-token\n\
tls:\n  ca: cacertificatedata\n  insecure: true\n\
export:\n\
\x20 power:\n\
\x20   type: jumpstarter_driver_power.driver.PduPower\n\
\x20   config:\n\
\x20     host: 192.168.1.111\n\
\x20     port: 1234\n\
\x20     auth:\n\
\x20       username: admin\n\
\x20       password: secret\n\
\x20 serial:\n\
\x20   type: jumpstarter_driver_pyserial.driver.Pyserial\n\
\x20   config:\n\
\x20     port: /dev/ttyUSB0\n\
\x20     baudrate: 115200\n\
\x20 nested:\n\
\x20   children:\n\
\x20     custom:\n\
\x20       type: vendorpackage.CustomDriver\n\
\x20       config:\n\
\x20         hello: world\n";

    #[test]
    fn parses_driver_tree() {
        let c = ExporterConfig::from_yaml(EXPORTER_YAML).unwrap();
        assert_eq!(c.metadata.name, "test");
        assert_eq!(c.tls.ca, "cacertificatedata");
        assert!(c.tls.insecure);
        assert_eq!(c.export.len(), 3);

        // power: a concrete Base driver with kwargs.
        let DriverInstance::Base(power) = &c.export["power"] else {
            panic!("power should be a Base driver, got {:?}", c.export["power"]);
        };
        assert_eq!(power.r#type, "jumpstarter_driver_power.driver.PduPower");
        assert_eq!(power.config["host"], serde_json::json!("192.168.1.111"));
        assert_eq!(power.config["port"], serde_json::json!(1234));
        assert_eq!(power.config["auth"], serde_json::json!({"username": "admin", "password": "secret"}));

        // nested: a Base node carrying a `custom` child (Python treats a node with
        // children-and-no-type structurally; here it has children only).
        let custom = match &c.export["nested"] {
            DriverInstance::Base(b) => &b.children,
            DriverInstance::Composite(comp) => &comp.children,
            other => panic!("unexpected nested variant: {other:?}"),
        };
        let DriverInstance::Base(inner) = &custom["custom"] else {
            panic!("custom should be a Base driver");
        };
        assert_eq!(inner.r#type, "vendorpackage.CustomDriver");
        assert_eq!(inner.config["hello"], serde_json::json!("world"));
    }

    #[test]
    fn round_trips_driver_tree() {
        let c = ExporterConfig::from_yaml(EXPORTER_YAML).unwrap();
        let reparsed = ExporterConfig::from_yaml(&c.to_yaml().unwrap()).unwrap();
        assert_eq!(c, reparsed);
    }

    #[test]
    fn parses_before_and_after_lease_hooks() {
        let yaml = "apiVersion: jumpstarter.dev/v1alpha1\n\
kind: ExporterConfig\n\
metadata:\n  namespace: default\n  name: test-hooks\n\
endpoint: jumpstarter.my-lab.com:1443\n\
token: t\n\
hooks:\n\
\x20 beforeLease:\n\
\x20   script: |\n\
\x20     echo pre\n\
\x20     j power on\n\
\x20   timeout: 600\n\
\x20 afterLease:\n\
\x20   script: |\n\
\x20     echo post\n\
\x20     j power off\n\
\x20   timeout: 600\n\
export:\n\
\x20 power:\n\
\x20   type: jumpstarter_driver_power.driver.PduPower\n";
        let c = ExporterConfig::from_yaml(yaml).unwrap();
        let before = c.hooks.before_lease.as_ref().expect("beforeLease");
        let after = c.hooks.after_lease.as_ref().expect("afterLease");
        assert_eq!(before.script, "echo pre\nj power on\n");
        assert_eq!(before.timeout, 600);
        assert_eq!(after.script, "echo post\nj power off\n");

        // The save round-trip keeps the camelCase hook keys.
        let out = c.to_yaml().unwrap();
        assert!(out.contains("beforeLease:"), "{out}");
        assert!(out.contains("afterLease:"), "{out}");
        assert!(!out.contains("before_lease:"));
    }
}
