//! Exporter-config → resolved device tree (proto-only resolution).
//!
//! Walks an [`ExporterConfig`]'s `export:` tree and resolves every driver node to its gRPC
//! interface **without loading any driver code**: the node's explicit `interface:` key wins,
//! else the committed driver registry (`interfaces/registry/`) maps the node's `type:` string;
//! the interface's `.proto` (from the registry, or derived from the FQN by the package-path
//! convention) is compiled in-process with protox. Unresolvable nodes become `Opaque` — warned
//! and skipped by the device emitters, or a hard error under `strict`.

use std::collections::BTreeMap;
use std::path::Path;

use jumpstarter_config::{DriverInstance, DriverRegistry, ExporterConfig};

/// One resolved interface: the proto contract a device node speaks.
#[derive(Debug, Clone)]
pub struct ResolvedInterface {
    /// Service full name, e.g. `jumpstarter.interfaces.power.v1.PowerInterface`.
    pub fqn: String,
    /// Proto source path relative to the proto root.
    pub proto_path: String,
    /// The protox-compiled, self-contained `FileDescriptorSet`.
    pub descriptor_set: Vec<u8>,
}

/// A node in the resolved device tree (mirrors the config's `export:`/`children:` shape).
#[derive(Debug, Clone)]
pub struct ResolvedNode {
    /// The config key — becomes the report's `jumpstarter.dev/name` label.
    pub name: String,
    /// Full name path from the export root (`["dut", "power"]`).
    pub path: Vec<String>,
    pub kind: NodeKind,
    pub children: Vec<ResolvedNode>,
}

#[derive(Debug, Clone)]
pub enum NodeKind {
    /// A driver with a resolved interface.
    Driver {
        /// Interface FQN (keys into [`ResolvedDevice::interfaces`]).
        interface: String,
        /// The config's `type:` string.
        driver_type: String,
        /// Advertised custom clients per language (from the registry).
        clients: BTreeMap<String, String>,
    },
    /// A pure grouping node (config `children:`-only entry).
    Composite,
    /// A node the resolver could not type — skipped by emitters.
    Opaque { reason: String },
}

/// The fully resolved device: the tree + the deduped interface set it references.
#[derive(Debug)]
pub struct ResolvedDevice {
    /// PascalCase base name for the generated wrapper (`<name>Device`).
    pub device_name: String,
    /// Top-level `export:` entries, in config (BTreeMap) order.
    pub roots: Vec<ResolvedNode>,
    /// Interface FQN → compiled contract, deduped across the tree.
    pub interfaces: BTreeMap<String, ResolvedInterface>,
    /// Human-readable resolution warnings (opaque nodes, registry misses, …).
    pub warnings: Vec<String>,
}

/// `power-board` / `power_board` / `powerBoard` → `PowerBoard`.
pub fn pascal_case(name: &str) -> String {
    let mut out = String::new();
    let mut upper_next = true;
    for c in name.chars() {
        if c == '-' || c == '_' || c == '.' || c == ' ' {
            upper_next = true;
        } else if upper_next {
            out.extend(c.to_uppercase());
            upper_next = false;
        } else {
            out.push(c);
        }
    }
    out
}

/// Derive the conventional proto path from an interface FQN when the registry has no entry:
/// `jumpstarter.interfaces.power.v1.PowerInterface` → `jumpstarter/interfaces/power/v1/power.proto`
/// (the file stem is the last package segment that isn't a `vN` version segment).
pub fn proto_path_for_fqn(fqn: &str) -> Option<String> {
    let (package, _service) = fqn.rsplit_once('.')?;
    let segments: Vec<&str> = package.split('.').collect();
    let stem = segments.iter().rev().find(|s| {
        !(s.starts_with('v') && s[1..].chars().all(|c| c.is_ascii_digit()) && s.len() > 1)
    })?;
    Some(format!("{}/{}.proto", segments.join("/"), stem))
}

/// Resolve an exporter config against a registry + proto root.
pub fn resolve_device(
    config: &ExporterConfig,
    registry: &DriverRegistry,
    proto_root: &Path,
    strict: bool,
) -> anyhow::Result<ResolvedDevice> {
    let mut device = ResolvedDevice {
        device_name: pascal_case(&config.metadata.name),
        roots: Vec::new(),
        interfaces: BTreeMap::new(),
        warnings: Vec::new(),
    };

    let export = &config.export;
    for (name, instance) in export {
        let node = resolve_node(
            name,
            instance,
            &[],
            export,
            registry,
            proto_root,
            &mut device,
        )?;
        device.roots.push(node);
    }

    if strict {
        let mut opaque = Vec::new();
        collect_opaque(&device.roots, &mut opaque);
        if !opaque.is_empty() {
            anyhow::bail!("unresolved driver nodes (strict): {}", opaque.join("; "));
        }
    }
    Ok(device)
}

fn collect_opaque(nodes: &[ResolvedNode], out: &mut Vec<String>) {
    for node in nodes {
        if let NodeKind::Opaque { reason } = &node.kind {
            out.push(format!("{}: {}", node.path.join("/"), reason));
        }
        collect_opaque(&node.children, out);
    }
}

#[allow(clippy::too_many_arguments)]
fn resolve_node(
    name: &str,
    instance: &DriverInstance,
    parent_path: &[String],
    export: &BTreeMap<String, DriverInstance>,
    registry: &DriverRegistry,
    proto_root: &Path,
    device: &mut ResolvedDevice,
) -> anyhow::Result<ResolvedNode> {
    let mut path = parent_path.to_vec();
    path.push(name.to_string());

    let (kind, children_map): (NodeKind, Option<&BTreeMap<String, DriverInstance>>) = match instance
    {
        DriverInstance::Base(base) => {
            let kind = resolve_driver_kind(
                &path,
                &base.r#type,
                base.interface.as_deref(),
                registry,
                proto_root,
                device,
            )?;
            (kind, Some(&base.children))
        }
        DriverInstance::Composite(composite) => (NodeKind::Composite, Some(&composite.children)),
        DriverInstance::Proxy(proxy) => {
            // Top-level `ref:` target reuse: resolve the dotted ref through the export tree
            // and adopt the target driver's typing under the proxy's name (the runtime
            // proxy delegates to the target). Deeper proxy semantics are deferred.
            let kind = match lookup_ref(export, &proxy.reference) {
                Some(DriverInstance::Base(target)) => resolve_driver_kind(
                    &path,
                    &target.r#type,
                    target.interface.as_deref(),
                    registry,
                    proto_root,
                    device,
                )?,
                Some(_) => opaque_kind(
                    device,
                    &path,
                    format!("proxy ref {:?} targets a non-driver node", proxy.reference),
                ),
                None => opaque_kind(
                    device,
                    &path,
                    format!("proxy ref {:?} not found in export tree", proxy.reference),
                ),
            };
            (kind, None)
        }
    };

    let mut children = Vec::new();
    if let Some(map) = children_map {
        for (child_name, child) in map {
            children.push(resolve_node(
                child_name, child, &path, export, registry, proto_root, device,
            )?);
        }
    }

    Ok(ResolvedNode {
        name: name.to_string(),
        path,
        kind,
        children,
    })
}

/// Follow a dotted `ref:` path (`"dut.power"`) from the export root through `children:` maps.
fn lookup_ref<'a>(
    export: &'a BTreeMap<String, DriverInstance>,
    reference: &str,
) -> Option<&'a DriverInstance> {
    let mut segments = reference.split('.');
    let mut node = export.get(segments.next()?)?;
    for segment in segments {
        let children = match node {
            DriverInstance::Base(b) => &b.children,
            DriverInstance::Composite(c) => &c.children,
            DriverInstance::Proxy(_) => return None,
        };
        node = children.get(segment)?;
    }
    Some(node)
}

fn opaque_kind(device: &mut ResolvedDevice, path: &[String], reason: String) -> NodeKind {
    device
        .warnings
        .push(format!("{}: {}", path.join("/"), reason));
    NodeKind::Opaque { reason }
}

fn resolve_driver_kind(
    path: &[String],
    driver_type: &str,
    explicit_interface: Option<&str>,
    registry: &DriverRegistry,
    proto_root: &Path,
    device: &mut ResolvedDevice,
) -> anyhow::Result<NodeKind> {
    let registry_entry = registry.driver(driver_type);
    let fqn = match explicit_interface.or(registry_entry.as_ref().map(|(i, _)| i.name.as_str())) {
        Some(fqn) => fqn.to_string(),
        None => {
            return Ok(opaque_kind(
                device,
                path,
                format!(
                    "type {driver_type:?} has no registry entry and no `interface:` key — \
                     add one of them for a typed binding"
                ),
            ));
        }
    };

    if !device.interfaces.contains_key(&fqn) {
        let proto_path = match registry
            .proto_for(&fqn)
            .map(str::to_string)
            .or_else(|| proto_path_for_fqn(&fqn))
        {
            Some(p) => p,
            None => {
                return Ok(opaque_kind(
                    device,
                    path,
                    format!("cannot derive a proto path for interface {fqn:?}"),
                ));
            }
        };
        let proto_file = proto_root.join(&proto_path);
        if !proto_file.is_file() {
            return Ok(opaque_kind(
                device,
                path,
                format!(
                    "interface {fqn:?}: proto not found at {}",
                    proto_file.display()
                ),
            ));
        }
        let fds = protox::compile([proto_file.as_path()], [proto_root])
            .map_err(|e| anyhow::anyhow!("protox compile {}: {e}", proto_file.display()))?;
        let bytes = prost::Message::encode_to_vec(&fds);
        // The FQN must actually exist in the compiled contract (catches registry typos).
        let has_service = fds.file.iter().any(|f| {
            f.service.iter().any(|s| {
                format!(
                    "{}.{}",
                    f.package.as_deref().unwrap_or(""),
                    s.name.as_deref().unwrap_or("")
                ) == fqn
            })
        });
        if !has_service {
            return Ok(opaque_kind(
                device,
                path,
                format!("interface {fqn:?} not declared by {proto_path}"),
            ));
        }
        device.interfaces.insert(
            fqn.clone(),
            ResolvedInterface {
                fqn: fqn.clone(),
                proto_path,
                descriptor_set: bytes,
            },
        );
    }

    Ok(NodeKind::Driver {
        interface: fqn,
        driver_type: driver_type.to_string(),
        clients: registry_entry
            .map(|(_, clients)| clients)
            .unwrap_or_default(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use jumpstarter_config::YamlConfig;

    fn repo_proto_root() -> std::path::PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../interfaces/proto")
    }

    fn registry() -> DriverRegistry {
        DriverRegistry::from_yaml(
            r#"
version: 1
interfaces:
  - name: jumpstarter.interfaces.power.v1.PowerInterface
    proto: jumpstarter/interfaces/power/v1/power.proto
    drivers:
      - name: jumpstarter_driver_power.driver.MockPower
        clients:
          python: jumpstarter_driver_power.client.PowerClient
"#,
        )
        .unwrap()
    }

    fn config(yaml_export: &str) -> ExporterConfig {
        ExporterConfig::from_yaml(&format!(
            "apiVersion: jumpstarter.dev/v1alpha1\nkind: ExporterConfig\n\
             metadata:\n  namespace: default\n  name: example-device\n\
             endpoint: e:1\ntoken: t\nexport:\n{yaml_export}"
        ))
        .unwrap()
    }

    #[test]
    fn resolves_registry_and_interface_key_nodes_and_dedups() {
        let cfg = config(
            "  dut:\n    children:\n      power:\n        type: jumpstarter_driver_power.driver.MockPower\n\
             \x20     native:\n        type: rust:whatever\n        interface: jumpstarter.interfaces.power.v1.PowerInterface\n",
        );
        let device = resolve_device(&cfg, &registry(), &repo_proto_root(), true).unwrap();
        assert_eq!(device.device_name, "ExampleDevice");
        assert_eq!(device.interfaces.len(), 1, "same interface deduped");
        let dut = &device.roots[0];
        assert!(matches!(dut.kind, NodeKind::Composite));
        assert_eq!(dut.children.len(), 2);
        let native = dut.children.iter().find(|c| c.name == "native").unwrap();
        assert!(matches!(native.kind, NodeKind::Driver { .. }));
        let power = dut.children.iter().find(|c| c.name == "power").unwrap();
        assert_eq!(power.path, vec!["dut".to_string(), "power".to_string()]);
        match &power.kind {
            NodeKind::Driver { clients, .. } => {
                assert_eq!(
                    clients.get("python").map(String::as_str),
                    Some("jumpstarter_driver_power.client.PowerClient")
                );
            }
            other => panic!("expected driver, got {other:?}"),
        }
    }

    #[test]
    fn unknown_type_is_opaque_and_strict_errors() {
        let cfg = config("  mystery:\n    type: some.unknown.Driver\n");
        let device = resolve_device(&cfg, &registry(), &repo_proto_root(), false).unwrap();
        assert!(matches!(device.roots[0].kind, NodeKind::Opaque { .. }));
        assert_eq!(device.warnings.len(), 1);
        assert!(resolve_device(&cfg, &registry(), &repo_proto_root(), true).is_err());
    }

    #[test]
    fn proxy_adopts_target_typing() {
        let cfg = config(
            "  power:\n    type: jumpstarter_driver_power.driver.MockPower\n\
             \x20 alias:\n    ref: power\n",
        );
        let device = resolve_device(&cfg, &registry(), &repo_proto_root(), true).unwrap();
        let alias = device.roots.iter().find(|n| n.name == "alias").unwrap();
        match &alias.kind {
            NodeKind::Driver { interface, .. } => {
                assert_eq!(interface, "jumpstarter.interfaces.power.v1.PowerInterface");
            }
            other => panic!("expected driver, got {other:?}"),
        }
    }

    #[test]
    fn conventional_proto_path_derivation() {
        assert_eq!(
            proto_path_for_fqn("jumpstarter.interfaces.power.v1.PowerInterface").as_deref(),
            Some("jumpstarter/interfaces/power/v1/power.proto")
        );
        assert_eq!(
            proto_path_for_fqn("jumpstarter.interfaces.storage_mux.v1.StorageMuxInterface")
                .as_deref(),
            Some("jumpstarter/interfaces/storage_mux/v1/storage_mux.proto")
        );
    }

    #[test]
    fn missing_proto_file_is_opaque_not_fatal() {
        let cfg = config(
            "  ghost:\n    type: rust:ghost\n    interface: jumpstarter.interfaces.ghost.v1.GhostInterface\n",
        );
        let device = resolve_device(&cfg, &registry(), &repo_proto_root(), false).unwrap();
        assert!(matches!(device.roots[0].kind, NodeKind::Opaque { .. }));
    }
}
