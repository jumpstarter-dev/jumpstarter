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
    /// Per-interface client SELECTION: a map from a proto interface full name (e.g.
    /// `jumpstarter.interfaces.power.v1.PowerInterface`) to a client selector — a `rust:<crate>`
    /// binary, a `jvm:<fqn>`, or a Python client class path — that OVERRIDES the driver's advertised
    /// `jumpstarter.dev/client` for that interface. Lets a site pin a language-specific client (e.g.
    /// drive a Rust driver with a JVM client). Empty by default, so the advertised client is used —
    /// fully backwards compatible (a config without this key, or an interface not listed, is
    /// unchanged).
    #[serde(default, skip_serializing_if = "std::collections::BTreeMap::is_empty")]
    pub select: std::collections::BTreeMap<String, String>,
}

impl DriversConfig {
    /// The client selector to drive `interface` with: the per-interface [`select`](Self::select)
    /// override if one is configured, else the driver's `advertised` `jumpstarter.dev/client`.
    pub fn select_client<'a>(&'a self, interface: &str, advertised: &'a str) -> &'a str {
        self.select
            .get(interface)
            .map(String::as_str)
            .unwrap_or(advertised)
    }
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

// Ported from the deleted Python `config/client_config_test.py` (the
// ClientConfigV1Alpha1 model is owned by Rust now): YAML parsing, the
// exclude_none / leases-default-drop save format, and round-trip.
#[cfg(test)]
mod tests {
    use super::*;
    use crate::YamlConfig;

    const TOKEN: &str = "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz";

    fn from_file_yaml() -> String {
        format!(
            "apiVersion: jumpstarter.dev/v1alpha1\n\
             kind: ClientConfig\n\
             metadata:\n  namespace: default\n  name: testclient\n\
             endpoint: jumpstarter.my-lab.com:1443\n\
             token: {TOKEN}\n\
             drivers:\n  allow:\n  - jumpstarter.drivers.*\n  - vendorpackage.*\n  unsafe: false\n"
        )
    }

    #[test]
    fn parses_fields_from_yaml() {
        let c = ClientConfig::from_yaml(&from_file_yaml()).unwrap();
        assert_eq!(c.metadata.namespace.as_deref(), Some("default"));
        assert_eq!(c.metadata.name, "testclient");
        assert_eq!(c.endpoint.as_deref(), Some("jumpstarter.my-lab.com:1443"));
        assert_eq!(c.token.as_deref(), Some(TOKEN));
        assert_eq!(c.drivers.allow, vec!["jumpstarter.drivers.*", "vendorpackage.*"]);
        assert!(!c.drivers.r#unsafe);
    }

    #[test]
    fn round_trips_through_yaml() {
        let c = ClientConfig::from_yaml(&from_file_yaml()).unwrap();
        let reparsed = ClientConfig::from_yaml(&c.to_yaml().unwrap()).unwrap();
        assert_eq!(c, reparsed);
    }

    #[test]
    fn per_interface_client_select_is_backwards_compatible() {
        // A config WITHOUT `select` parses (empty map) and the advertised client is used — the
        // existing behaviour is unchanged.
        let c = ClientConfig::from_yaml(&from_file_yaml()).unwrap();
        assert!(c.drivers.select.is_empty());
        assert_eq!(
            c.drivers.select_client("jumpstarter.interfaces.power.v1.PowerInterface", "rust:foo"),
            "rust:foo",
            "absent select -> advertised client"
        );
        // `select` is omitted from the saved YAML when empty (no churn for old configs).
        assert!(!c.to_yaml().unwrap().contains("select:"));

        // A config WITH `select` overrides the advertised client per interface, and round-trips.
        let yaml = format!(
            "apiVersion: jumpstarter.dev/v1alpha1\nkind: ClientConfig\n\
             metadata:\n  namespace: default\n  name: c\n\
             endpoint: e:1\ntoken: {TOKEN}\n\
             drivers:\n  select:\n    jumpstarter.interfaces.power.v1.PowerInterface: jvm:com.example.PowerClient\n"
        );
        let c = ClientConfig::from_yaml(&yaml).unwrap();
        assert_eq!(
            c.drivers.select_client("jumpstarter.interfaces.power.v1.PowerInterface", "rust:foo"),
            "jvm:com.example.PowerClient",
            "select override wins over the advertised client"
        );
        // An interface NOT in `select` still falls back to the advertised client.
        assert_eq!(c.drivers.select_client("other.Interface", "rust:foo"), "rust:foo");
        assert_eq!(ClientConfig::from_yaml(&c.to_yaml().unwrap()).unwrap(), c);
    }

    #[test]
    fn save_omits_none_fields_and_default_leases() {
        // A minimal config: no endpoint/token, all-default leases.
        let c = ClientConfig::new(ObjectMeta::new("c1").with_namespace("default"));
        let yaml = c.to_yaml().unwrap();
        // exclude_none: absent optionals are not serialized.
        assert!(!yaml.contains("token:"), "token should be omitted when None:\n{yaml}");
        assert!(!yaml.contains("endpoint:"), "endpoint should be omitted when None:\n{yaml}");
        assert!(!yaml.contains("refresh_token:"));
        // leases is dropped entirely when it holds only defaults (v0.7.x compat).
        assert!(!yaml.contains("leases:"), "default leases block should be dropped:\n{yaml}");
        // camelCase keys are preserved.
        assert!(yaml.contains("apiVersion:"));
        assert!(yaml.contains("grpcOptions:"));
    }

    #[test]
    fn save_keeps_non_default_leases() {
        let mut c = ClientConfig::new(ObjectMeta::new("c1"));
        c.leases.acquisition_timeout = 99;
        let yaml = c.to_yaml().unwrap();
        assert!(yaml.contains("leases:"), "non-default leases must be kept:\n{yaml}");
        assert!(yaml.contains("acquisition_timeout: 99"));
    }

    #[test]
    fn drivers_default_to_empty_and_safe() {
        let c = ClientConfig::from_yaml(
            "apiVersion: jumpstarter.dev/v1alpha1\nkind: ClientConfig\nmetadata:\n  name: c\n",
        )
        .unwrap();
        assert!(c.drivers.allow.is_empty());
        assert!(!c.drivers.r#unsafe);
        assert_eq!(c.leases.acquisition_timeout, 7200);
    }
}
