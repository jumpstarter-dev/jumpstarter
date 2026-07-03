//! The driver registry — the static map from exporter-config `type:` strings to proto
//! interfaces, consulted by `jumpstarter-codegen --kind device` so a config node resolves to
//! its gRPC contract **without loading any driver code** (proto-only resolution).
//!
//! Registry files are YAML, generated-and-committed under `interfaces/registry/`:
//! `python.yaml` is emitted at maintainer time by `python -m jumpstarter.driver.proto_gen
//! generate-all --registry-out …` (the only place driver modules are imported — trusted,
//! reviewed output); `native.yaml` is hand-maintained for `rust:`/`jvm:` driver types. All three
//! runtimes share one key space (the exporter config's `type:` string verbatim).
//!
//! ```yaml
//! version: 1
//! drivers:
//!   jumpstarter_driver_power.driver.MockPower:
//!     interface: jumpstarter.interfaces.power.v1.PowerInterface
//!     clients:
//!       python: jumpstarter_driver_power.client.PowerClient
//! interfaces:
//!   jumpstarter.interfaces.power.v1.PowerInterface:
//!     proto: jumpstarter/interfaces/power/v1/power.proto
//! ```

use std::collections::BTreeMap;
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::ConfigError;

fn version_default() -> u32 {
    1
}

/// One driver `type:`'s registry entry.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DriverRegistryEntry {
    /// The proto service full name (`jumpstarter.interfaces.power.v1.PowerInterface`).
    pub interface: String,
    /// Advertised custom clients per language (`python` / `rust` / `jvm` → selector: a Python
    /// dotted class path, a Rust type path, or a JVM FQN). Languages without an entry use the
    /// generated typed client.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub clients: BTreeMap<String, String>,
}

/// One interface FQN's registry entry.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct InterfaceRegistryEntry {
    /// The interface's `.proto` source, relative to the proto root (`interfaces/proto/`).
    pub proto: String,
}

/// A parsed driver registry (one file, or several merged — see [`DriverRegistry::merge`]).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct DriverRegistry {
    #[serde(default = "version_default")]
    pub version: u32,
    /// Exporter-config `type:` string → interface + advertised clients.
    #[serde(default)]
    pub drivers: BTreeMap<String, DriverRegistryEntry>,
    /// Interface FQN → proto source path.
    #[serde(default)]
    pub interfaces: BTreeMap<String, InterfaceRegistryEntry>,
}

impl DriverRegistry {
    /// Merge `other` into `self`; on duplicate keys `other` wins (load order is deterministic —
    /// see [`DriverRegistry::load_path`] — so later files intentionally override earlier ones).
    pub fn merge(&mut self, other: DriverRegistry) {
        self.drivers.extend(other.drivers);
        self.interfaces.extend(other.interfaces);
    }

    /// Load a registry from a YAML file, or from every `*.yaml`/`*.yml` directly inside a
    /// directory (merged in filename order).
    pub fn load_path(path: &Path) -> Result<Self, ConfigError> {
        use crate::YamlConfig;

        if !path.is_dir() {
            return Self::from_yaml(&std::fs::read_to_string(path)?);
        }
        let mut files: Vec<_> = std::fs::read_dir(path)?
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| {
                matches!(
                    p.extension().and_then(|e| e.to_str()),
                    Some("yaml") | Some("yml")
                )
            })
            .collect();
        files.sort();
        let mut merged = DriverRegistry::default();
        for file in files {
            merged.merge(Self::from_yaml(&std::fs::read_to_string(file)?)?);
        }
        Ok(merged)
    }

    /// The registry entry for an exporter-config `type:` string.
    pub fn driver(&self, driver_type: &str) -> Option<&DriverRegistryEntry> {
        self.drivers.get(driver_type)
    }

    /// The proto source path (relative to the proto root) for an interface FQN.
    pub fn proto_for(&self, interface: &str) -> Option<&str> {
        self.interfaces.get(interface).map(|e| e.proto.as_str())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::YamlConfig;

    const PYTHON_YAML: &str = "\
version: 1
drivers:
  jumpstarter_driver_power.driver.MockPower:
    interface: jumpstarter.interfaces.power.v1.PowerInterface
    clients:
      python: jumpstarter_driver_power.client.PowerClient
interfaces:
  jumpstarter.interfaces.power.v1.PowerInterface:
    proto: jumpstarter/interfaces/power/v1/power.proto
";

    const NATIVE_YAML: &str = "\
version: 1
drivers:
  rust:power:
    interface: jumpstarter.interfaces.power.v1.PowerInterface
  jvm:dev.jumpstarter.examples.power.KotlinPowerDriver:
    interface: jumpstarter.interfaces.power.v1.PowerInterface
    clients:
      jvm: dev.jumpstarter.examples.power.CyclingPowerClient
";

    #[test]
    fn parses_and_answers_lookups() {
        let r = DriverRegistry::from_yaml(PYTHON_YAML).unwrap();
        let entry = r.driver("jumpstarter_driver_power.driver.MockPower").unwrap();
        assert_eq!(entry.interface, "jumpstarter.interfaces.power.v1.PowerInterface");
        assert_eq!(
            entry.clients.get("python").map(String::as_str),
            Some("jumpstarter_driver_power.client.PowerClient")
        );
        assert_eq!(
            r.proto_for("jumpstarter.interfaces.power.v1.PowerInterface"),
            Some("jumpstarter/interfaces/power/v1/power.proto")
        );
        assert_eq!(r.driver("unknown.Type"), None);
        assert_eq!(r.proto_for("unknown.v1.Interface"), None);
    }

    #[test]
    fn merges_with_later_precedence() {
        let mut r = DriverRegistry::from_yaml(PYTHON_YAML).unwrap();
        r.merge(DriverRegistry::from_yaml(NATIVE_YAML).unwrap());
        assert!(r.driver("rust:power").is_some());
        assert!(r.driver("jumpstarter_driver_power.driver.MockPower").is_some());

        // Later merge overrides an existing driver entry.
        let override_yaml = "\
drivers:
  rust:power:
    interface: jumpstarter.interfaces.virtual_power.v1.VirtualPowerInterface
";
        r.merge(DriverRegistry::from_yaml(override_yaml).unwrap());
        assert_eq!(
            r.driver("rust:power").unwrap().interface,
            "jumpstarter.interfaces.virtual_power.v1.VirtualPowerInterface"
        );
    }

    #[test]
    fn loads_a_directory_merged_in_filename_order() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("a-python.yaml"), PYTHON_YAML).unwrap();
        std::fs::write(dir.path().join("b-native.yaml"), NATIVE_YAML).unwrap();
        std::fs::write(dir.path().join("ignored.txt"), "not yaml").unwrap();

        let r = DriverRegistry::load_path(dir.path()).unwrap();
        assert!(r.driver("jumpstarter_driver_power.driver.MockPower").is_some());
        assert!(r.driver("rust:power").is_some());
        assert!(r.driver("jvm:dev.jumpstarter.examples.power.KotlinPowerDriver").is_some());

        // A single file loads directly too.
        let single = DriverRegistry::load_path(&dir.path().join("a-python.yaml")).unwrap();
        assert!(single.driver("rust:power").is_none());
    }

    #[test]
    fn defaults_are_lenient() {
        // An empty document is a valid (empty) registry; version defaults to 1.
        let r = DriverRegistry::from_yaml("{}").unwrap();
        assert_eq!(r.version, 1);
        assert!(r.drivers.is_empty());
    }
}
