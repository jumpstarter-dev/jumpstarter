//! Device-wrapper codegen (`--kind device`): from a [`ResolvedDevice`] emit, per language, the
//! typed per-interface clients (reusing the existing [`LanguageGenerator`] emitters verbatim)
//! plus one device wrapper class mirroring the exporter config's named tree.
//!
//! Per driver node the wrapper binds, in precedence order: the client-config
//! `drivers.select[<interface FQN>]` override → the registry-advertised custom client for the
//! target language → the generated typed client. Custom clients subclass (Python/Kotlin) or wrap
//! via `with_uuid` (Rust) the generated ones.

use std::collections::BTreeMap;
use std::fmt::Write as _;

use crate::engine::interfaces_from_descriptor_set;
use crate::ir::InterfaceRef;
use crate::languages::{self, LanguageGenerator};
use crate::resolver::{pascal_case, NodeKind, ResolvedDevice, ResolvedNode};

/// How one driver node binds to a client class in the target language.
#[derive(Debug, Clone, PartialEq, Eq)]
enum Binding {
    /// The codegen-generated typed client for the node's interface.
    Generated,
    /// A hand-written client (import path / type path / FQN, language-appropriate).
    Custom(String),
}

/// Options for one device generation run.
#[derive(Debug, Default, Clone)]
pub struct DeviceOptions {
    /// Override the wrapper base name (default: PascalCase of `metadata.name`).
    pub device_name: Option<String>,
    /// Interface FQN → client selector overrides (`DriversConfig.select` + `--select`).
    pub select: BTreeMap<String, String>,
    /// Dotted Python package the generated modules live in (mirrors `--python-package`).
    pub python_package: Option<String>,
}

/// The language a client selector targets, by prefix convention (`rust:` / `jvm:` / Python path).
fn selector_language(selector: &str) -> &'static str {
    if selector.starts_with("rust:") {
        "rust"
    } else if selector.starts_with("jvm:") {
        "jvm"
    } else {
        "python"
    }
}

/// Strip a selector's language prefix to the raw class/type path.
fn selector_path(selector: &str) -> &str {
    selector
        .strip_prefix("rust:")
        .or_else(|| selector.strip_prefix("jvm:"))
        .unwrap_or(selector)
}

/// Resolve one node's binding for the target language (see module docs for precedence).
fn binding_for(
    language: &str,
    interface_fqn: &str,
    advertised: &BTreeMap<String, String>,
    opts: &DeviceOptions,
    warnings: &mut Vec<String>,
) -> Binding {
    if let Some(selector) = opts.select.get(interface_fqn) {
        if selector_language(selector) == language {
            let path = selector_path(selector).to_string();
            if language == "rust" && !path.contains("::") {
                warnings.push(format!(
                    "select {selector:?} for {interface_fqn}: a Rust device binding needs a type \
                     path (`rust:crate::path::Type` implementing `with_uuid`), not a CLI registry \
                     name — using the generated client"
                ));
            } else {
                return Binding::Custom(path);
            }
        }
        // A selector for another language is simply not for this target.
    }
    if let Some(advertised) = advertised.get(language) {
        return Binding::Custom(selector_path(advertised).to_string());
    }
    Binding::Generated
}

/// Generate the device wrapper + its per-interface typed clients for one language.
///
/// Returns `(relative file path → contents, warnings)`.
pub fn generate_device(
    device: &ResolvedDevice,
    language: &str,
    opts: &DeviceOptions,
) -> anyhow::Result<(BTreeMap<String, String>, Vec<String>)> {
    let mut warnings = device.warnings.clone();
    // The class IS the root driver client, named for the exporter (`example-rig` → `ExampleRig`).
    let device_name = opts
        .device_name
        .clone()
        .unwrap_or_else(|| device.device_name.clone());

    // Interface FQN → the walked IR (+ raw descriptor bytes for the Python embedding).
    let mut ifaces: BTreeMap<String, (InterfaceRef, Vec<u8>)> = BTreeMap::new();
    for (fqn, resolved) in &device.interfaces {
        let all = interfaces_from_descriptor_set(&resolved.descriptor_set)?;
        let iface = all
            .into_iter()
            .find(|i| format!("{}.{}", i.proto_package, i.service_name) == *fqn)
            .ok_or_else(|| anyhow::anyhow!("interface {fqn} not found in its descriptor set"))?;
        ifaces.insert(fqn.clone(), (iface, resolved.descriptor_set.clone()));
    }

    let mut files: BTreeMap<String, String> = BTreeMap::new();
    // The per-interface typed clients — the existing generators, unchanged.
    for (iface, descriptor_set) in ifaces.values() {
        let generator: Box<dyn LanguageGenerator> = match language {
            "python" => Box::new(languages::python::PythonGenerator::new(
                opts.python_package.clone(),
                descriptor_set.clone(),
            )),
            "rust" => Box::new(languages::rust::RustGenerator),
            "java" | "kotlin" => Box::new(languages::java::JavaGenerator),
            other => anyhow::bail!("unsupported device language {other:?}"),
        };
        files.extend(generator.generate_client(iface));
    }

    let device_source = match language {
        "python" => render_device_python(device, &device_name, &ifaces, opts, &mut warnings),
        "rust" => render_device_rust(device, &device_name, &ifaces, opts, &mut warnings),
        "java" | "kotlin" => render_device_kotlin(device, &device_name, &ifaces, opts, &mut warnings),
        other => anyhow::bail!("unsupported device language {other:?}"),
    };
    let device_file = match language {
        "python" => "device.py".to_string(),
        "rust" => "device.rs".to_string(),
        _ => format!("{device_name}.kt"),
    };
    files.insert(device_file, device_source);

    Ok((files, warnings))
}

/// Depth-first walk collecting every typed driver node with its language binding.
fn walk<'a>(
    nodes: &'a [ResolvedNode],
    language: &str,
    opts: &DeviceOptions,
    warnings: &mut Vec<String>,
    out: &mut Vec<(&'a ResolvedNode, String, Binding)>,
) {
    for node in nodes {
        if let NodeKind::Driver { interface, clients, .. } = &node.kind {
            let binding = binding_for(language, interface, clients, opts, warnings);
            out.push((node, interface.clone(), binding));
        }
        walk(&node.children, language, opts, warnings, out);
    }
}

/// The shape of the generated root class. The class IS the root driver's typed client — a config
/// with one export entry roots directly on that entry (no wrapper hop), a multi-entry config
/// roots on the exporter's composite root with one attribute per entry.
enum RootShape<'a> {
    /// One composite export entry: its children become the class's direct attributes.
    SingleComposite(&'a ResolvedNode),
    /// One driver export entry: the class IS that driver's typed client.
    SingleDriver(&'a ResolvedNode),
    /// Multiple export entries (or none typed): entries as direct attributes of the root.
    Forest(&'a [ResolvedNode]),
}

fn root_shape(device: &ResolvedDevice) -> RootShape<'_> {
    if device.roots.len() == 1 {
        let root = &device.roots[0];
        match root.kind {
            NodeKind::Driver { .. } => return RootShape::SingleDriver(root),
            NodeKind::Composite => return RootShape::SingleComposite(root),
            NodeKind::Opaque { .. } => {}
        }
    }
    RootShape::Forest(&device.roots)
}

// --------------------------------------------------------------------------------------------
// Python

/// The 8 DriverClient constructor kwargs, forwarded from an existing node client (the Python
/// equivalent of rebinding — see `jumpstarter.client.base.rebind_client`).
const PY_NODE_KWARGS: &str = "uuid=node.uuid, labels=node.labels, session=node.session,\n            portal=node.portal, stack=node.stack, children=node.children,\n            description=node.description, methods_description=node.methods_description,";

fn render_device_python(
    device: &ResolvedDevice,
    device_name: &str,
    ifaces: &BTreeMap<String, (InterfaceRef, Vec<u8>)>,
    opts: &DeviceOptions,
    warnings: &mut Vec<String>,
) -> String {
    let mut drivers = Vec::new();
    walk(&device.roots, "python", opts, warnings, &mut drivers);

    // Imports: generated clients relatively, custom clients absolutely.
    let mut generated_imports: BTreeMap<String, String> = BTreeMap::new(); // module → class
    let mut custom_imports: BTreeMap<String, String> = BTreeMap::new(); // module → class
    let mut node_class: BTreeMap<Vec<String>, String> = BTreeMap::new(); // path → class expr
    for (node, fqn, binding) in &drivers {
        let class = match binding {
            Binding::Generated => {
                let (iface, _) = &ifaces[fqn];
                let class = languages::python::client_class_name(iface);
                let module = format!("{}_client", languages::python::module_stem(iface));
                generated_imports.insert(module, class.clone());
                class
            }
            Binding::Custom(path) => {
                let (module, class) = path.rsplit_once('.').unwrap_or(("", path.as_str()));
                if !module.is_empty() {
                    custom_imports.insert(module.to_string(), class.to_string());
                }
                class.to_string()
            }
        };
        node_class.insert(node.path.clone(), class);
    }

    let shape = root_shape(device);

    let mut s = String::new();
    let _ = write!(
        s,
        "# @generated by jumpstarter-codegen (device). DO NOT EDIT.\n\
         #\n\
         # The typed root client for this exporter config: the class IS the root driver node,\n\
         # constructed from the client handle `serve(...)`, `env()`, or `jmp shell` yields. Each\n\
         # named child is rebound to its typed client — custom where one is defined, else the\n\
         # generated typed client — so the tree works even when a node's driver package isn't\n\
         # installed client-side.\n\n\
         from jumpstarter.client.base import DriverClient, rebind_client, resolve_root_child\n"
    );
    for (module, class) in &custom_imports {
        let _ = writeln!(s, "from {module} import {class}");
    }
    for (module, class) in &generated_imports {
        let _ = writeln!(s, "from .{module} import {class}");
    }

    // Class name: "_" + PascalCase of the node path ("dut"/"sub" → _DutSub).
    fn composite_class_name(path: &[String]) -> String {
        format!(
            "_{}",
            path.iter().map(|p| pascal_case(p)).collect::<Vec<_>>().join("")
        )
    }

    // One line per child, binding it onto `self` from `node.children`.
    fn assign_lines(
        children: &[ResolvedNode],
        node_class: &BTreeMap<Vec<String>, String>,
    ) -> Vec<String> {
        let mut lines = Vec::new();
        for child in children {
            match &child.kind {
                NodeKind::Driver { .. } => {
                    let class = &node_class[&child.path];
                    lines.push(format!(
                        "self.{name}: {class} = rebind_client(node.children[\"{name}\"], {class})",
                        name = child.name,
                    ));
                }
                NodeKind::Composite => {
                    let class = composite_class_name(&child.path);
                    lines.push(format!(
                        "self.{name} = {class}(node.children[\"{name}\"])",
                        name = child.name,
                    ));
                }
                NodeKind::Opaque { reason } => {
                    lines.push(format!("# {name}: skipped ({reason})", name = child.name));
                }
            }
        }
        lines
    }

    // Nested composite groups: typed DriverClient subclasses over their node, deepest-first so
    // references exist textually. The root node itself is rendered as the device class below.
    fn collect_composites<'a>(
        nodes: &'a [ResolvedNode],
        skip_root: Option<&'a ResolvedNode>,
        out: &mut Vec<&'a ResolvedNode>,
    ) {
        for node in nodes {
            collect_composites(&node.children, None, out);
            if matches!(node.kind, NodeKind::Composite)
                && !skip_root.is_some_and(|root| std::ptr::eq(root, node))
            {
                out.push(node);
            }
        }
    }
    let skip_root = match shape {
        RootShape::SingleComposite(root) => Some(root),
        _ => None,
    };
    let mut composites = Vec::new();
    collect_composites(&device.roots, skip_root, &mut composites);

    for composite in &composites {
        let class = composite_class_name(&composite.path);
        let _ = write!(
            s,
            "\n\nclass {class}(DriverClient):\n    \
                 \"\"\"`{path}` composite group (generated).\"\"\"\n\n    \
                 def __init__(self, node: DriverClient):\n        \
                     super().__init__(\n            {PY_NODE_KWARGS}\n        )\n",
            path = composite.path.join("/"),
        );
        for line in assign_lines(&composite.children, &node_class) {
            let _ = writeln!(s, "        {line}");
        }
    }

    match shape {
        RootShape::SingleDriver(root) => {
            // The device class IS the root driver's typed client.
            let class = &node_class[&root.path];
            let _ = write!(
                s,
                "\n\nclass {device_name}({class}):\n    \
                     \"\"\"The `{name}` driver, typed (generated) — the whole exporter tree.\"\"\"\n\n    \
                     def __init__(self, root: DriverClient):\n        \
                         node = resolve_root_child(root, \"{name}\")\n        \
                         super().__init__(\n            {PY_NODE_KWARGS}\n        )\n",
                name = root.name,
            );
        }
        RootShape::SingleComposite(root) => {
            let _ = write!(
                s,
                "\n\nclass {device_name}(DriverClient):\n    \
                     \"\"\"The `{name}` driver tree, typed (generated).\"\"\"\n\n    \
                     def __init__(self, root: DriverClient):\n        \
                         node = resolve_root_child(root, \"{name}\")\n        \
                         super().__init__(\n            {PY_NODE_KWARGS}\n        )\n",
                name = root.name,
            );
            for line in assign_lines(&root.children, &node_class) {
                let _ = writeln!(s, "        {line}");
            }
        }
        RootShape::Forest(roots) => {
            let _ = write!(
                s,
                "\n\nclass {device_name}(DriverClient):\n    \
                     \"\"\"Typed root client for this exporter config (generated).\"\"\"\n\n    \
                     def __init__(self, node: DriverClient):\n        \
                         super().__init__(\n            {PY_NODE_KWARGS}\n        )\n"
            );
            for root in roots {
                match &root.kind {
                    NodeKind::Driver { .. } => {
                        let class = &node_class[&root.path];
                        let _ = writeln!(
                            s,
                            "        self.{name}: {class} = rebind_client(resolve_root_child(node, \"{name}\"), {class})",
                            name = root.name,
                        );
                    }
                    NodeKind::Composite => {
                        let class = composite_class_name(&root.path);
                        let _ = writeln!(
                            s,
                            "        self.{name} = {class}(resolve_root_child(node, \"{name}\"))",
                            name = root.name,
                        );
                    }
                    NodeKind::Opaque { reason } => {
                        let _ = writeln!(s, "        # {name}: skipped ({reason})", name = root.name);
                    }
                }
            }
        }
    }
    s
}

// --------------------------------------------------------------------------------------------
// Rust

fn render_device_rust(
    device: &ResolvedDevice,
    device_name: &str,
    ifaces: &BTreeMap<String, (InterfaceRef, Vec<u8>)>,
    opts: &DeviceOptions,
    warnings: &mut Vec<String>,
) -> String {
    let mut drivers = Vec::new();
    walk(&device.roots, "rust", opts, warnings, &mut drivers);
    let mut node_type: BTreeMap<Vec<String>, String> = BTreeMap::new();
    for (node, fqn, binding) in &drivers {
        let ty = match binding {
            Binding::Generated => {
                let (iface, _) = &ifaces[fqn];
                format!(
                    "{}Client",
                    languages::rust::strip_interface_suffix(&iface.service_name)
                )
            }
            Binding::Custom(path) => path.clone(),
        };
        node_type.insert(node.path.clone(), ty);
    }

    fn composite_struct_name(device_name: &str, path: &[String]) -> String {
        format!(
            "{device_name}{}",
            path.iter().map(|p| pascal_case(p)).collect::<Vec<_>>().join("")
        )
    }

    let mut s = String::new();
    let _ = write!(
        s,
        "// @generated by jumpstarter-codegen (device). DO NOT EDIT.\n\
         //\n\
         // The typed root client for this exporter config: one `get_report`, a tree-aware\n\
         // name-path index, and per-node typed clients bound by uuid.\n\n\
         // The sibling generated clients (re-exported next to this module by the aggregator).\n\
         #[allow(unused_imports)]\n\
         use super::*;\n\n"
    );

    let shape = root_shape(device);

    if let RootShape::SingleDriver(root) = shape {
        // The root IS one driver: `connect` yields its typed client directly.
        let ty = &node_type[&root.path];
        let _ = write!(
            s,
            "/// The `{name}` driver, typed (generated) — the whole exporter tree.\n\
             pub struct {device_name};\n\n\
             impl {device_name} {{\n    \
                 /// Resolve the `{name}` node from the session's report and bind its typed client.\n    \
                 pub async fn connect<'a>(\n        \
                     session: &'a ::jumpstarter_client::ClientSession,\n    \
                 ) -> Result<{ty}<'a>, ::jumpstarter_codec::error::DriverCallError> {{\n        \
                     let index = ::jumpstarter_client::DriverReportIndex::from_session(session).await?;\n        \
                     Ok({ty}::with_uuid(session, index.resolve_path(&[\"{name}\"])?))\n    \
                 }}\n\
             }}\n",
            name = root.name,
        );
        return s;
    }

    // Struct-tree shapes: the device struct's fields are the root node's children (single
    // composite entry) or the export entries themselves (forest).
    fn collect_composites<'a>(
        nodes: &'a [ResolvedNode],
        skip_root: Option<&'a ResolvedNode>,
        out: &mut Vec<&'a ResolvedNode>,
    ) {
        for node in nodes {
            collect_composites(&node.children, None, out);
            if matches!(node.kind, NodeKind::Composite)
                && !skip_root.is_some_and(|root| std::ptr::eq(root, node))
            {
                out.push(node);
            }
        }
    }
    let skip_root = match shape {
        RootShape::SingleComposite(root) => Some(root),
        _ => None,
    };
    let mut composites = Vec::new();
    collect_composites(&device.roots, skip_root, &mut composites);

    fn struct_fields(
        s: &mut String,
        device_name: &str,
        children: &[ResolvedNode],
        node_type: &BTreeMap<Vec<String>, String>,
    ) {
        for child in children {
            match &child.kind {
                NodeKind::Driver { .. } => {
                    let _ = writeln!(s, "    pub {}: {}<'a>,", child.name, node_type[&child.path]);
                }
                NodeKind::Composite => {
                    let _ = writeln!(
                        s,
                        "    pub {}: {}<'a>,",
                        child.name,
                        composite_struct_name(device_name, &child.path)
                    );
                }
                NodeKind::Opaque { reason } => {
                    let _ = writeln!(s, "    // {}: skipped ({reason})", child.name);
                }
            }
        }
    }

    for composite in &composites {
        let name = composite_struct_name(device_name, &composite.path);
        let _ = writeln!(s, "/// `{}` composite group (generated).", composite.path.join("/"));
        let _ = writeln!(s, "pub struct {name}<'a> {{");
        struct_fields(&mut s, device_name, &composite.children, &node_type);
        let _ = writeln!(s, "}}\n");
    }

    let root_children: &[ResolvedNode] = match shape {
        RootShape::SingleComposite(root) => &root.children,
        _ => &device.roots,
    };
    let _ = writeln!(s, "/// The typed root client for this exporter config (generated).");
    let _ = writeln!(s, "pub struct {device_name}<'a> {{");
    struct_fields(&mut s, device_name, root_children, &node_type);
    let _ = writeln!(s, "}}\n");

    fn init_expr(
        node: &ResolvedNode,
        device_name: &str,
        node_type: &BTreeMap<Vec<String>, String>,
    ) -> Option<String> {
        let path_args = node
            .path
            .iter()
            .map(|p| format!("\"{p}\""))
            .collect::<Vec<_>>()
            .join(", ");
        match &node.kind {
            NodeKind::Driver { .. } => Some(format!(
                "{}::with_uuid(session, index.resolve_path(&[{path_args}])?)",
                node_type[&node.path]
            )),
            NodeKind::Composite => {
                let fields: Vec<String> = node
                    .children
                    .iter()
                    .filter_map(|child| {
                        init_expr(child, device_name, node_type)
                            .map(|expr| format!("{}: {expr}", child.name))
                    })
                    .collect();
                Some(format!(
                    "{} {{ {} }}",
                    composite_struct_name(device_name, &node.path),
                    fields.join(", ")
                ))
            }
            NodeKind::Opaque { .. } => None,
        }
    }

    let _ = write!(
        s,
        "impl<'a> {device_name}<'a> {{\n    \
             /// Bind the device tree from one `GetReport` over the session.\n    \
             pub async fn new(\n        \
                 session: &'a ::jumpstarter_client::ClientSession,\n    \
             ) -> Result<{device_name}<'a>, ::jumpstarter_codec::error::DriverCallError> {{\n        \
                 let index = ::jumpstarter_client::DriverReportIndex::from_session(session).await?;\n        \
                 Ok({device_name} {{\n"
    );
    for child in root_children {
        if let Some(expr) = init_expr(child, device_name, &node_type) {
            let _ = writeln!(s, "            {}: {expr},", child.name);
        }
    }
    let _ = write!(s, "        }})\n    }}\n}}\n");
    s
}

// --------------------------------------------------------------------------------------------
// Kotlin

fn render_device_kotlin(
    device: &ResolvedDevice,
    device_name: &str,
    ifaces: &BTreeMap<String, (InterfaceRef, Vec<u8>)>,
    opts: &DeviceOptions,
    warnings: &mut Vec<String>,
) -> String {
    let mut drivers = Vec::new();
    walk(&device.roots, "jvm", opts, warnings, &mut drivers);
    let mut node_type: BTreeMap<Vec<String>, String> = BTreeMap::new();
    for (node, fqn, binding) in &drivers {
        let ty = match binding {
            Binding::Generated => {
                let (iface, _) = &ifaces[fqn];
                format!(
                    "dev.jumpstarter.generated.{}.{}",
                    languages::java::generated_subpackage(iface),
                    languages::java::client_class_name(iface)
                )
            }
            Binding::Custom(path) => path.clone(),
        };
        node_type.insert(node.path.clone(), ty);
    }

    fn camel(name: &str) -> String {
        let pascal = pascal_case(name);
        let mut chars = pascal.chars();
        match chars.next() {
            Some(c) => c.to_lowercase().collect::<String>() + chars.as_str(),
            None => pascal,
        }
    }

    let mut s = String::new();
    let _ = write!(
        s,
        "// @generated by jumpstarter-codegen (device). DO NOT EDIT.\n\
         //\n\
         // The typed root client for this exporter config; every node resolves by NAME PATH\n\
         // from the session's report (tree-aware, duplicate-name safe).\n\
         package dev.jumpstarter.generated.device\n\n\
         import dev.jumpstarter.client.ExporterSession\n\n"
    );

    let shape = root_shape(device);

    if let RootShape::SingleDriver(root) = shape {
        // The root IS one driver: a factory function yields its typed client directly.
        let ty = &node_type[&root.path];
        let _ = write!(
            s,
            "/** The `{name}` driver, typed (generated) — the whole exporter tree. */\n\
             @Suppress(\"FunctionName\")\n\
             fun {device_name}(session: ExporterSession): {ty} =\n    \
                 {ty}(session, session.requireDriverPath(\"{name}\"))\n",
            name = root.name,
        );
        return s;
    }

    fn render_class(
        s: &mut String,
        children: &[ResolvedNode],
        node_type: &BTreeMap<Vec<String>, String>,
        camel: &dyn Fn(&str) -> String,
        indent: usize,
    ) {
        let pad = "    ".repeat(indent);
        for child in children {
            match &child.kind {
                NodeKind::Driver { .. } => {
                    let path_args = child
                        .path
                        .iter()
                        .map(|p| format!("\"{p}\""))
                        .collect::<Vec<_>>()
                        .join(", ");
                    let _ = writeln!(
                        s,
                        "{pad}    val {}: {ty} = {ty}(session, session.requireDriverPath({path_args}))",
                        camel(&child.name),
                        ty = node_type[&child.path],
                    );
                }
                NodeKind::Composite => {
                    let nested = pascal_case(&child.name);
                    let _ = writeln!(
                        s,
                        "{pad}    val {}: {nested} = {nested}(session)",
                        camel(&child.name),
                    );
                }
                NodeKind::Opaque { reason } => {
                    let _ = writeln!(s, "{pad}    // {}: skipped ({reason})", child.name);
                }
            }
        }
        // Nested classes for composite children, after the fields.
        for child in children {
            if matches!(child.kind, NodeKind::Composite) {
                let nested = pascal_case(&child.name);
                let _ = writeln!(s, "\n{pad}    class {nested}(session: ExporterSession) {{");
                render_class(s, &child.children, node_type, camel, indent + 1);
                let _ = writeln!(s, "{pad}    }}");
            }
        }
    }

    let root_children: &[ResolvedNode] = match shape {
        RootShape::SingleComposite(root) => &root.children,
        _ => &device.roots,
    };
    let _ = writeln!(
        s,
        "/** The typed root client for this exporter config (generated). */\nopen class {device_name}(session: ExporterSession) {{"
    );
    render_class(&mut s, root_children, &node_type, &camel, 0);
    let _ = writeln!(s, "}}");
    s
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::resolver::resolve_device;
    use jumpstarter_config::{DriverRegistry, ExporterConfig, YamlConfig};
    use std::path::Path;

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
      - name: jvm:dev.jumpstarter.examples.power.KotlinPowerDriver
        clients:
          jvm: dev.jumpstarter.examples.power.CyclingPowerClient
"#,
        )
        .unwrap()
    }

    fn device_for(export_yaml: &str) -> ResolvedDevice {
        let config = ExporterConfig::from_yaml(&format!(
            "apiVersion: jumpstarter.dev/v1alpha1\nkind: ExporterConfig\n\
             metadata:\n  namespace: default\n  name: example\n\
             endpoint: e:1\ntoken: t\nexport:\n{export_yaml}"
        ))
        .unwrap();
        resolve_device(&config, &registry(), &repo_proto_root(), false).unwrap()
    }

    /// Multi-entry (forest) fixture: a composite + an opaque top-level entry.
    fn fixture_forest() -> ResolvedDevice {
        device_for(
            "  dut:\n    children:\n      power:\n        type: jumpstarter_driver_power.driver.MockPower\n\
             \x20     backup_power:\n        type: jvm:dev.jumpstarter.examples.power.KotlinPowerDriver\n\
             \x20 mystery:\n    type: unknown.Driver\n",
        )
    }

    /// The common rig shape: ONE composite export entry.
    fn fixture_single_composite() -> ResolvedDevice {
        device_for(
            "  dut:\n    children:\n      power:\n        type: jumpstarter_driver_power.driver.MockPower\n\
             \x20     backup_power:\n        type: jvm:dev.jumpstarter.examples.power.KotlinPowerDriver\n",
        )
    }

    /// ONE driver export entry: the device IS that driver's client.
    fn fixture_single_driver() -> ResolvedDevice {
        device_for("  power:\n    type: jvm:dev.jumpstarter.examples.power.KotlinPowerDriver\n")
    }

    #[test]
    fn python_single_composite_root_is_the_entry_itself() {
        let (files, _) =
            generate_device(&fixture_single_composite(), "python", &DeviceOptions::default())
                .unwrap();
        let src = &files["device.py"];
        // The class IS the dut node's client — children are DIRECT attributes (no .dut. hop).
        assert!(src.contains("class Example(DriverClient):"), "{src}");
        assert!(src.contains("node = resolve_root_child(root, \"dut\")"), "{src}");
        assert!(src.contains("self.power: PowerClient = rebind_client(node.children[\"power\"], PowerClient)"), "{src}");
        // The advertised python custom client wins; jvm-only labels don't leak into python.
        assert!(src.contains("from jumpstarter_driver_power.client import PowerClient"), "{src}");
        assert!(src.contains("from .power_client import PowerClient"), "{src}");
        // The single composite entry gets NO extra wrapper class.
        assert!(!src.contains("class _Dut"), "{src}");
        assert!(files.contains_key("power_client.py"), "{:?}", files.keys());
    }

    #[test]
    fn python_single_driver_root_subclasses_the_client() {
        let (files, _) =
            generate_device(&fixture_single_driver(), "python", &DeviceOptions::default()).unwrap();
        let src = &files["device.py"];
        // The device class IS the driver's typed client (generated one — jvm label ≠ python).
        assert!(src.contains("class Example(PowerClient):"), "{src}");
        assert!(src.contains("node = resolve_root_child(root, \"power\")"), "{src}");
    }

    #[test]
    fn python_forest_keeps_entries_as_attributes_and_skips_opaque() {
        let (files, warnings) =
            generate_device(&fixture_forest(), "python", &DeviceOptions::default()).unwrap();
        let src = &files["device.py"];
        assert!(src.contains("class Example(DriverClient):"), "{src}");
        assert!(src.contains("self.dut = _Dut(resolve_root_child(node, \"dut\"))"), "{src}");
        // Composite groups are typed DriverClient subclasses over their node.
        assert!(src.contains("class _Dut(DriverClient):"), "{src}");
        assert!(src.contains("# mystery: skipped"), "{src}");
        assert!(warnings.iter().any(|w| w.contains("mystery")), "{warnings:?}");
    }

    #[test]
    fn select_override_beats_advertised_label() {
        let opts = DeviceOptions {
            select: BTreeMap::from([(
                "jumpstarter.interfaces.power.v1.PowerInterface".to_string(),
                "my_pkg.clients.SuperPowerClient".to_string(),
            )]),
            ..Default::default()
        };
        let (files, _) = generate_device(&fixture_single_composite(), "python", &opts).unwrap();
        let src = &files["device.py"];
        assert!(src.contains("from my_pkg.clients import SuperPowerClient"), "{src}");
        assert!(!src.contains("from jumpstarter_driver_power.client import"), "{src}");
    }

    #[test]
    fn rust_single_composite_flattens_and_resolves_full_paths() {
        let (files, _) =
            generate_device(&fixture_single_composite(), "rust", &DeviceOptions::default()).unwrap();
        let src = &files["device.rs"];
        assert!(src.contains("pub struct Example<'a> {"), "{src}");
        // Children are direct fields; report paths stay FULL ("dut"/"power").
        assert!(src.contains("pub power: PowerClient<'a>,"), "{src}");
        assert!(src.contains("PowerClient::with_uuid(session, index.resolve_path(&[\"dut\", \"power\"])?)"), "{src}");
        assert!(!src.contains("pub dut:"), "{src}");
        assert!(files.contains_key("power_client.rs"), "{:?}", files.keys());
    }

    #[test]
    fn rust_single_driver_connect_returns_the_client() {
        let (files, _) =
            generate_device(&fixture_single_driver(), "rust", &DeviceOptions::default()).unwrap();
        let src = &files["device.rs"];
        assert!(src.contains("pub struct Example;"), "{src}");
        assert!(src.contains("pub async fn connect<'a>"), "{src}");
        assert!(src.contains("Ok(PowerClient::with_uuid(session, index.resolve_path(&[\"power\"])?))"), "{src}");
    }

    #[test]
    fn kotlin_single_composite_uses_advertised_jvm_client_and_name_paths() {
        let (files, _) =
            generate_device(&fixture_single_composite(), "kotlin", &DeviceOptions::default())
                .unwrap();
        let src = &files["Example.kt"];
        assert!(src.contains("package dev.jumpstarter.generated.device"), "{src}");
        assert!(src.contains("open class Example(session: ExporterSession)"), "{src}");
        // Children are direct vals; the jvm advertised custom client binds backup_power.
        assert!(
            src.contains("dev.jumpstarter.examples.power.CyclingPowerClient(session, session.requireDriverPath(\"dut\", \"backup_power\"))"),
            "{src}"
        );
        assert!(
            src.contains("dev.jumpstarter.generated.power.PowerClient(session, session.requireDriverPath(\"dut\", \"power\"))"),
            "{src}"
        );
        assert!(src.contains("val backupPower:"), "{src}");
        assert!(!src.contains("val dut:"), "{src}");
        assert!(files.contains_key("PowerClient.kt"), "{:?}", files.keys());
    }

    #[test]
    fn kotlin_single_driver_is_a_factory_function() {
        let (files, _) =
            generate_device(&fixture_single_driver(), "kotlin", &DeviceOptions::default()).unwrap();
        let src = &files["Example.kt"];
        assert!(src.contains("fun Example(session: ExporterSession): dev.jumpstarter.examples.power.CyclingPowerClient"), "{src}");
        assert!(src.contains("requireDriverPath(\"power\")"), "{src}");
    }

    #[test]
    fn rust_cli_registry_selector_is_rejected_with_warning() {
        let opts = DeviceOptions {
            select: BTreeMap::from([(
                "jumpstarter.interfaces.power.v1.PowerInterface".to_string(),
                "rust:powercli".to_string(),
            )]),
            ..Default::default()
        };
        let (files, warnings) = generate_device(&fixture_single_composite(), "rust", &opts).unwrap();
        assert!(files["device.rs"].contains("PowerClient::with_uuid"), "generated fallback");
        assert!(warnings.iter().any(|w| w.contains("type path")), "{warnings:?}");
    }
}
