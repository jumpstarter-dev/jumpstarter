//! The driver registry — the static, INTERFACE-keyed map consulted by
//! `jumpstarter-codegen --kind device` so an exporter-config node resolves to its gRPC contract
//! **without loading any driver code** (proto-only resolution).
//!
//! The interface is the source of truth: each entry names one proto service, its `.proto`
//! source, and the driver `type:` strings (all three runtimes share one namespace — Python
//! dotted paths, `rust:<crate>`, `jvm:<fqn>`) known to implement it, each optionally advertising
//! per-language custom clients. Registry files are YAML under `interfaces/registry/`:
//! `python.yaml` is emitted at maintainer time by `python -m jumpstarter.driver.proto_gen
//! generate-all --registry-out …` (the only place driver modules are imported — trusted,
//! reviewed output); `native.yaml` is hand-maintained for `rust:`/`jvm:` driver types.
//!
//! ```yaml
//! version: 1
//! interfaces:
//!   - name: jumpstarter.interfaces.power.v1.PowerInterface
//!     proto: jumpstarter/interfaces/power/v1/power.proto
//!     drivers:
//!       - name: jumpstarter_driver_power.driver.MockPower
//!         clients:
//!           python: jumpstarter_driver_power.client.PowerClient
//!       - jumpstarter_driver_power.driver_native.NativeMockPower   # bare-string shorthand
//!       - rust:jumpstarter-driver-power-pure
//! ```

use std::collections::BTreeMap;
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::ConfigError;

fn version_default() -> u32 {
    1
}

/// One driver `type:` implementing an interface. A bare string is shorthand for
/// `{ name: <string> }` (no custom clients).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum RegistryDriver {
    /// Shorthand: just the driver `type:` string.
    Name(String),
    /// Full form: the driver `type:` plus advertised custom clients per language
    /// (`python` / `rust` / `jvm` → a Python dotted class path, a Rust type path, or a JVM FQN).
    Detailed {
        name: String,
        #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
        clients: BTreeMap<String, String>,
    },
}

impl RegistryDriver {
    /// The driver `type:` string (the exporter config's spelling).
    pub fn name(&self) -> &str {
        match self {
            RegistryDriver::Name(name) => name,
            RegistryDriver::Detailed { name, .. } => name,
        }
    }

    /// Advertised custom clients per language (empty for the shorthand form).
    pub fn clients(&self) -> BTreeMap<String, String> {
        match self {
            RegistryDriver::Name(_) => BTreeMap::new(),
            RegistryDriver::Detailed { clients, .. } => clients.clone(),
        }
    }
}

/// One interface entry — the unit of registry truth.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RegistryInterface {
    /// Proto service full name, e.g. `jumpstarter.interfaces.power.v1.PowerInterface`.
    pub name: String,
    /// The interface's `.proto` source, relative to the proto root (`interfaces/proto/`).
    pub proto: String,
    /// The driver `type:` strings known to implement this interface.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub drivers: Vec<RegistryDriver>,
}

/// A parsed driver registry (one file, or several merged — see [`DriverRegistry::merge`]).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct DriverRegistry {
    #[serde(default = "version_default")]
    pub version: u32,
    #[serde(default)]
    pub interfaces: Vec<RegistryInterface>,
}

impl DriverRegistry {
    /// Merge `other` into `self`, keyed by interface name: an interface present in both unions
    /// its driver lists (on a duplicate driver name `other` wins) and takes `other`'s proto.
    /// Load order is deterministic (see [`DriverRegistry::load_path`]), so later files
    /// intentionally override earlier ones.
    pub fn merge(&mut self, other: DriverRegistry) {
        for incoming in other.interfaces {
            match self.interfaces.iter_mut().find(|i| i.name == incoming.name) {
                Some(existing) => {
                    existing.proto = incoming.proto;
                    for driver in incoming.drivers {
                        existing.drivers.retain(|d| d.name() != driver.name());
                        existing.drivers.push(driver);
                    }
                }
                None => self.interfaces.push(incoming),
            }
        }
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

    /// The interface entry an exporter-config `type:` string implements (plus that driver's
    /// advertised clients).
    pub fn driver(
        &self,
        driver_type: &str,
    ) -> Option<(&RegistryInterface, BTreeMap<String, String>)> {
        for interface in &self.interfaces {
            if let Some(driver) = interface.drivers.iter().find(|d| d.name() == driver_type) {
                return Some((interface, driver.clients()));
            }
        }
        None
    }

    /// The interface entry for a proto service full name.
    pub fn interface(&self, name: &str) -> Option<&RegistryInterface> {
        self.interfaces.iter().find(|i| i.name == name)
    }

    /// The proto source path (relative to the proto root) for an interface FQN.
    pub fn proto_for(&self, interface: &str) -> Option<&str> {
        self.interface(interface).map(|i| i.proto.as_str())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::YamlConfig;

    const PYTHON_YAML: &str = "\
version: 1
interfaces:
  - name: jumpstarter.interfaces.power.v1.PowerInterface
    proto: jumpstarter/interfaces/power/v1/power.proto
    drivers:
      - name: jumpstarter_driver_power.driver.MockPower
        clients:
          python: jumpstarter_driver_power.client.PowerClient
      - jumpstarter_driver_power.driver_native.NativeMockPower
";

    const NATIVE_YAML: &str = "\
version: 1
interfaces:
  - name: jumpstarter.interfaces.power.v1.PowerInterface
    proto: jumpstarter/interfaces/power/v1/power.proto
    drivers:
      - rust:jumpstarter-driver-power-pure
      - name: jvm:dev.jumpstarter.examples.power.KotlinPowerDriver
        clients:
          jvm: dev.jumpstarter.examples.power.CyclingPowerClient
";

    #[test]
    fn parses_and_answers_lookups() {
        let r = DriverRegistry::from_yaml(PYTHON_YAML).unwrap();
        let (iface, clients) = r
            .driver("jumpstarter_driver_power.driver.MockPower")
            .unwrap();
        assert_eq!(iface.name, "jumpstarter.interfaces.power.v1.PowerInterface");
        assert_eq!(
            clients.get("python").map(String::as_str),
            Some("jumpstarter_driver_power.client.PowerClient")
        );
        // The bare-string shorthand: same interface, no custom clients.
        let (iface, clients) = r
            .driver("jumpstarter_driver_power.driver_native.NativeMockPower")
            .unwrap();
        assert_eq!(iface.name, "jumpstarter.interfaces.power.v1.PowerInterface");
        assert!(clients.is_empty());
        assert_eq!(
            r.proto_for("jumpstarter.interfaces.power.v1.PowerInterface"),
            Some("jumpstarter/interfaces/power/v1/power.proto")
        );
        assert!(r.driver("unknown.Type").is_none());
        assert_eq!(r.proto_for("unknown.v1.Interface"), None);
    }

    #[test]
    fn merges_by_interface_with_driver_union_and_later_precedence() {
        let mut r = DriverRegistry::from_yaml(PYTHON_YAML).unwrap();
        r.merge(DriverRegistry::from_yaml(NATIVE_YAML).unwrap());
        // Same interface entry now carries drivers from BOTH files.
        assert_eq!(r.interfaces.len(), 1);
        assert!(r.driver("rust:jumpstarter-driver-power-pure").is_some());
        assert!(r
            .driver("jumpstarter_driver_power.driver.MockPower")
            .is_some());
        let (_, clients) = r
            .driver("jvm:dev.jumpstarter.examples.power.KotlinPowerDriver")
            .unwrap();
        assert_eq!(
            clients.get("jvm").map(String::as_str),
            Some("dev.jumpstarter.examples.power.CyclingPowerClient")
        );

        // A later merge overrides an existing driver's entry (same driver name).
        let override_yaml = "\
interfaces:
  - name: jumpstarter.interfaces.power.v1.PowerInterface
    proto: jumpstarter/interfaces/power/v1/power.proto
    drivers:
      - name: rust:jumpstarter-driver-power-pure
        clients:
          rust: my_crate::CustomPowerClient
";
        r.merge(DriverRegistry::from_yaml(override_yaml).unwrap());
        let (_, clients) = r.driver("rust:jumpstarter-driver-power-pure").unwrap();
        assert_eq!(
            clients.get("rust").map(String::as_str),
            Some("my_crate::CustomPowerClient")
        );
    }

    #[test]
    fn loads_a_directory_merged_in_filename_order() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("a-python.yaml"), PYTHON_YAML).unwrap();
        std::fs::write(dir.path().join("b-native.yaml"), NATIVE_YAML).unwrap();
        std::fs::write(dir.path().join("ignored.txt"), "not yaml").unwrap();

        let r = DriverRegistry::load_path(dir.path()).unwrap();
        assert!(r
            .driver("jumpstarter_driver_power.driver.MockPower")
            .is_some());
        assert!(r.driver("rust:jumpstarter-driver-power-pure").is_some());

        // A single file loads directly too.
        let single = DriverRegistry::load_path(&dir.path().join("a-python.yaml")).unwrap();
        assert!(single
            .driver("rust:jumpstarter-driver-power-pure")
            .is_none());
    }

    #[test]
    fn defaults_are_lenient() {
        // An empty document is a valid (empty) registry; version defaults to 1.
        let r = DriverRegistry::from_yaml("{}").unwrap();
        assert_eq!(r.version, 1);
        assert!(r.interfaces.is_empty());
    }
}
