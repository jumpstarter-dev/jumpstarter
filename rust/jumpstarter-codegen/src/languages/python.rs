//! Python language generator.
//!
//! Emits, from one [`InterfaceRef`], the native (proto-first) Python glue for a Jumpstarter driver —
//! all stdlib, **no protobuf/grpcio/pydantic runtime types**: the proto⇄native marshalling lives in
//! `jumpstarter.driver.proto_marshal`, and message types are plain `@dataclass`es.
//!
//! - [`generate_driver`](PythonGenerator::generate_driver) — `<stem>_models.py` (the message
//!   dataclasses) + `<stem>_driver.py`, an abstract driver-interface base (native-typed
//!   `@abstractmethod`s + a `client()` classmethod) extending `jumpstarter.driver.ProtoInterface`,
//!   which IS a `Driver` and auto-exports implementations of the declared methods. The author
//!   subclasses ONLY the generated base — no `Driver` superclass, no `@export` — and the generic
//!   native host (proto_marshal) serves it; the `.proto` is the source of truth. A custom client is
//!   advertised with the `@jumpstarter.driver.driver(client=...)` class decorator (the analog of
//!   Rust's `#[driver(client = ...)]` and the JVM's `@JumpstarterDriver(client = ...)`).
//! - [`generate_client`](PythonGenerator::generate_client) — `<stem>_models.py` + `<stem>_client.py`,
//!   a typed `DriverClient` subclass whose methods drive the session's native (proto-bytes) seam and
//!   decode each response into the native dataclass.
//!
//! Fully IR-driven: a method's request-message fields become native parameters, the response message
//! is reversed to a native return type (Empty→`None`, a `<Method>Response{value}` wrapper→the scalar,
//! else the bare message dataclass), and the gRPC path is `/<proto_package>.<Service>/<Method>`.

use std::collections::BTreeMap;
use std::fmt::Write as _;

use crate::ir::{Field, InterfaceRef, Method};
use crate::languages::LanguageGenerator;

/// The Python per-language generator.
///
/// Unlike the Rust/JVM generators (where the embedding build compiles the `.proto` itself), the
/// Python side has no protoc step — so this generator also embeds the interface's serialized
/// `FileDescriptorSet` into a generated `<stem>_descriptor.py` (the analog of Rust's
/// `FILE_DESCRIPTOR_SET` in `OUT_DIR`), which the client runtime uses to encode/decode messages.
#[derive(Debug, Default, Clone)]
pub struct PythonGenerator {
    /// Dotted Python package the generated modules live in (e.g.
    /// `jumpstarter_driver_power._generated`) — used for the driver base's `client()` import path.
    package: Option<String>,
    /// The serialized `FileDescriptorSet` the interfaces were walked from, embedded verbatim into
    /// `<stem>_descriptor.py`.
    descriptor_set: Vec<u8>,
}

impl PythonGenerator {
    pub fn new(package: Option<String>, descriptor_set: Vec<u8>) -> Self {
        Self {
            package,
            descriptor_set,
        }
    }

    /// `"<package>."` when a package is set, else `""` — prefixes the `client()` import path.
    fn package_prefix(&self) -> String {
        self.package
            .as_ref()
            .map(|p| format!("{p}."))
            .unwrap_or_default()
    }
}

impl LanguageGenerator for PythonGenerator {
    fn name(&self) -> &str {
        "python"
    }

    fn generate_driver(&self, iface: &InterfaceRef) -> BTreeMap<String, String> {
        let mut out = BTreeMap::new();
        out.insert(models_file_name(iface), render_models(iface));
        out.insert(driver_file_name(iface), render_driver(iface, &self.package_prefix()));
        out.insert(
            descriptor_file_name(iface),
            render_descriptor(iface, &self.descriptor_set),
        );
        out
    }

    fn generate_client(&self, iface: &InterfaceRef) -> BTreeMap<String, String> {
        let mut out = BTreeMap::new();
        out.insert(models_file_name(iface), render_models(iface));
        out.insert(client_file_name(iface), render_client(iface));
        out.insert(
            descriptor_file_name(iface),
            render_descriptor(iface, &self.descriptor_set),
        );
        out
    }
}

// --------------------------------------------------------------------------------------------
// Naming helpers

/// `PascalCase` -> `snake_case` (`On` -> `on`, `SetVoltage` -> `set_voltage`). The driver's `@export`
/// method names + the client's method names are snake_case; the proto method is PascalCase.
fn pascal_to_snake(name: &str) -> String {
    let mut out = String::with_capacity(name.len() + 4);
    for (i, c) in name.chars().enumerate() {
        if c.is_ascii_uppercase() {
            if i != 0 {
                out.push('_');
            }
            out.push(c.to_ascii_lowercase());
        } else {
            out.push(c);
        }
    }
    out
}

/// Strip a trailing `Interface` from a service name (`PowerInterface` -> `Power`).
fn strip_interface_suffix(service: &str) -> String {
    service
        .strip_suffix("Interface")
        .unwrap_or(service)
        .to_string()
}

/// The snake_case module stem for the interface's generated files — the package segment just before
/// the trailing `vN` (`jumpstarter.interfaces.power.v1` -> `power`, `…storage_mux.v1` -> `storage_mux`).
pub(crate) fn module_stem(iface: &InterfaceRef) -> String {
    let segments: Vec<&str> = iface.proto_package.split('.').collect();
    segments
        .iter()
        .rev()
        .find(|s| !(s.starts_with('v') && s[1..].chars().all(|c| c.is_ascii_digit()) && s.len() > 1))
        .copied()
        .unwrap_or("interface")
        .to_string()
}

/// The typed client class name: `PowerInterface` -> `PowerClient`.
pub(crate) fn client_class_name(iface: &InterfaceRef) -> String {
    format!("{}Client", strip_interface_suffix(&iface.service_name))
}

fn models_file_name(iface: &InterfaceRef) -> String {
    format!("{}_models.py", module_stem(iface))
}
fn driver_file_name(iface: &InterfaceRef) -> String {
    format!("{}_driver.py", module_stem(iface))
}
fn client_file_name(iface: &InterfaceRef) -> String {
    format!("{}_client.py", module_stem(iface))
}
fn descriptor_file_name(iface: &InterfaceRef) -> String {
    format!("{}_descriptor.py", module_stem(iface))
}

/// The full gRPC method path: `/<proto_package>.<Service>/<Method>`.
fn method_path(iface: &InterfaceRef, method: &Method) -> String {
    format!("/{}.{}/{}", iface.proto_package, iface.service_name, method.name)
}

/// `true` for the well-known `google.protobuf.Empty`.
fn is_empty(proto_type: &str) -> bool {
    proto_type == "google.protobuf.Empty"
}

/// The short (unqualified) name of a proto type (`jumpstarter.interfaces.power.v1.PowerReading`
/// -> `PowerReading`).
fn short_name(full: &str) -> &str {
    full.rsplit('.').next().unwrap_or(full)
}

// --------------------------------------------------------------------------------------------
// Type mapping

/// Map a proto scalar type name to its native Python type.
fn py_scalar(scalar: &str) -> &'static str {
    match scalar {
        "double" | "float" => "float",
        "int32" | "int64" | "uint32" | "uint64" | "sint32" | "sint64" | "fixed32" | "fixed64"
        | "sfixed32" | "sfixed64" => "int",
        "bool" => "bool",
        "string" => "str",
        "bytes" => "bytes",
        _ => "object",
    }
}

/// The native Python type for a field's base (ignoring repeated/optional wrapping).
fn py_field_base(field: &Field) -> String {
    if field.is_message {
        match field.type_name.as_str() {
            "google.protobuf.Value" => "object".to_string(),
            "google.protobuf.Struct" => "dict[str, object]".to_string(),
            other => short_name(other).to_string(),
        }
    } else if field.is_enum {
        // No enum codegen yet (descriptor_builder can't yet pool nested enums); carry as int.
        "int".to_string()
    } else {
        py_scalar(&field.type_name).to_string()
    }
}

/// The native Python type for a field, applying `repeated` (`list[T]`) and proto3 `optional`
/// (`T | None`) wrapping.
fn py_field_type(field: &Field) -> String {
    let base = py_field_base(field);
    if field.repeated {
        format!("list[{base}]")
    } else if field.optional {
        format!("{base} | None")
    } else {
        base
    }
}

/// The native default expression for a proto3 field (its zero value), so the generated dataclass is
/// trivially constructible.
fn py_field_default(field: &Field) -> String {
    if field.repeated {
        "field(default_factory=list)".to_string()
    } else if field.optional || field.is_message {
        "None".to_string()
    } else if field.is_enum {
        "0".to_string()
    } else {
        match field.type_name.as_str() {
            "double" | "float" => "0.0".to_string(),
            "bool" => "False".to_string(),
            "string" => "\"\"".to_string(),
            "bytes" => "b\"\"".to_string(),
            _ => "0".to_string(),
        }
    }
}

/// Whether a message is a synthetic `<Method>Response` scalar wrapper — exactly one field named
/// `value`. Its return maps to that field's native type rather than a dataclass.
fn wrapper_value_field<'a>(iface: &'a InterfaceRef, output_type: &str) -> Option<&'a Field> {
    let msg = iface.messages.iter().find(|m| m.full_name == output_type)?;
    match msg.fields.as_slice() {
        [only] if only.name == "value" => Some(only),
        _ => None,
    }
}

/// The native return type for a method (before server-streaming wrapping): Empty→`None`, a
/// `<Method>Response{value}` wrapper→the scalar type, else the bare message dataclass.
fn py_return_base(iface: &InterfaceRef, method: &Method) -> String {
    if is_empty(&method.output_type) {
        return "None".to_string();
    }
    if let Some(value) = wrapper_value_field(iface, &method.output_type) {
        return py_field_type(value);
    }
    short_name(&method.output_type).to_string()
}

/// The native parameter list for a method (excluding `self`): the request message's fields, in
/// order. Empty request → no params.
fn py_params(iface: &InterfaceRef, method: &Method) -> Vec<(String, String)> {
    if is_empty(&method.input_type) {
        return Vec::new();
    }
    let Some(msg) = iface.messages.iter().find(|m| m.full_name == method.input_type) else {
        return Vec::new();
    };
    msg.fields
        .iter()
        .map(|f| (f.name.clone(), py_field_type(f)))
        .collect()
}

/// The message dataclass names an interface's methods reference in their signatures (params +
/// bare-message returns), so the driver/client files import exactly those from `<stem>_models`.
fn referenced_models(iface: &InterfaceRef) -> Vec<String> {
    let mut names: Vec<String> = Vec::new();
    let mut push = |n: String| {
        if !names.contains(&n) {
            names.push(n);
        }
    };
    for m in &iface.methods {
        // Bare-message return.
        if !is_empty(&m.output_type) && wrapper_value_field(iface, &m.output_type).is_none() {
            push(short_name(&m.output_type).to_string());
        }
        // Message-typed params.
        for msg in iface.messages.iter().filter(|msg| msg.full_name == m.input_type) {
            for f in &msg.fields {
                if f.is_message
                    && f.type_name != "google.protobuf.Value"
                    && f.type_name != "google.protobuf.Struct"
                {
                    push(short_name(&f.type_name).to_string());
                }
            }
        }
    }
    names.sort();
    names
}

// --------------------------------------------------------------------------------------------
// Models

fn render_models(iface: &InterfaceRef) -> String {
    let service = &iface.service_name;
    // `field(default_factory=list)` is only needed when some message has a repeated field.
    let needs_field = iface
        .messages
        .iter()
        .any(|m| m.fields.iter().any(|f| f.repeated));
    let import = if needs_field {
        "from dataclasses import dataclass, field"
    } else {
        "from dataclasses import dataclass"
    };
    let mut s = String::new();
    let _ = write!(
        s,
        "# @generated by jumpstarter-codegen (PythonGenerator). DO NOT EDIT.\n\
         #\n\
         # Native message dataclasses for the `{service}` interface — plain stdlib dataclasses (no\n\
         # protobuf/pydantic types). The proto ⇄ native marshalling lives in\n\
         # jumpstarter.driver.proto_marshal.\n\n\
         {import}\n\n\n"
    );
    if iface.messages.is_empty() {
        s.push_str("# (this interface declares no message types)\n");
        return s;
    }
    for (i, msg) in iface.messages.iter().enumerate() {
        if i != 0 {
            s.push_str("\n\n");
        }
        let _ = writeln!(s, "@dataclass\nclass {}:", msg.name);
        if msg.fields.is_empty() {
            s.push_str("    pass\n");
            continue;
        }
        for f in &msg.fields {
            let _ = writeln!(
                s,
                "    {}: {} = {}",
                f.name,
                py_field_type(f),
                py_field_default(f)
            );
        }
    }
    s
}

// --------------------------------------------------------------------------------------------
// Descriptor module — the embedded FileDescriptorSet (the analog of Rust's FILE_DESCRIPTOR_SET)

fn render_descriptor(iface: &InterfaceRef, descriptor_set: &[u8]) -> String {
    let service = &iface.service_name;
    let full = format!("{}.{}", iface.proto_package, service);
    // Hex (not base64) so decoding is a stdlib one-liner with no import. Wrapped as adjacent
    // string literals for a readable file.
    let hex: String = descriptor_set
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    let mut s = String::new();
    let _ = write!(
        s,
        "# @generated by jumpstarter-codegen (PythonGenerator). DO NOT EDIT.\n\
         #\n\
         # The serialized FileDescriptorSet the `{service}` modules were generated from — the wire\n\
         # contract the typed client encodes/decodes against (the analog of the Rust crates'\n\
         # embedded FILE_DESCRIPTOR_SET).\n\n\
         SERVICE_FULL_NAME = \"{full}\"\n\n\
         FILE_DESCRIPTOR_SET: bytes = bytes.fromhex(\n"
    );
    for chunk in hex.as_bytes().chunks(96) {
        let _ = writeln!(s, "    \"{}\"", std::str::from_utf8(chunk).unwrap_or_default());
    }
    s.push_str(")\n");
    s
}

// --------------------------------------------------------------------------------------------
// Driver interface base (the proto-first authoring surface)

fn render_driver(iface: &InterfaceRef, package_prefix: &str) -> String {
    let service = &iface.service_name;
    let stem = module_stem(iface);
    let client = client_class_name(iface);
    let models = referenced_models(iface);

    let mut s = String::new();
    let _ = write!(
        s,
        "# @generated by jumpstarter-codegen (PythonGenerator). DO NOT EDIT.\n\
         #\n\
         # Proto-first driver interface for `{service}`, generated from its .proto (the source of\n\
         # truth). The base already IS a jumpstarter Driver: subclass it, implement each method\n\
         # (no @export — implementations of the declared methods are exported automatically), and\n\
         # the generic native host (jumpstarter.driver.proto_marshal) serves it. It advertises the\n\
         # generated typed client by default; override with @jumpstarter.driver.driver(client=...).\n\
         # Example:\n\
         #\n\
         #   class My{svc_stripped}({service}):\n\
         #       async def on(self) -> None: ...\n\n\
         from abc import abstractmethod\n",
        svc_stripped = strip_interface_suffix(service),
    );
    // Imports: AsyncIterator only if any server-streaming method; the runtime base; the models.
    if iface.methods.iter().any(|m| m.server_streaming) {
        s.push_str("from collections.abc import AsyncIterator\n");
    }
    s.push_str("\nfrom jumpstarter.driver import ProtoInterface\n");
    if !models.is_empty() {
        let _ = writeln!(s, "\nfrom .{stem}_models import {}", models.join(", "));
    }
    let _ = write!(
        s,
        "\n\nclass {service}(ProtoInterface):\n    \
             \"\"\"Proto-first `{service}` driver contract (generated). Subclass and implement.\"\"\"\n\n    \
             @classmethod\n    \
             def client(cls) -> str:\n        \
                 return \"{package_prefix}{stem}_client.{client}\"\n"
    );
    for m in &iface.methods {
        if m.client_streaming {
            continue; // client-/bidi-streaming out of scope for the proto-first milestone
        }
        let snake = pascal_to_snake(&m.name);
        let params = py_params(iface, m);
        let sig_params = render_sig_params(&params);
        let ret = py_return_base(iface, m);
        let ret_ty = if m.server_streaming {
            format!("AsyncIterator[{ret}]")
        } else {
            ret
        };
        let _ = write!(
            s,
            "\n    @abstractmethod\n    async def {snake}(self{sig_params}) -> {ret_ty}: ...\n"
        );
    }
    s
}

// --------------------------------------------------------------------------------------------
// Client

fn render_client(iface: &InterfaceRef) -> String {
    let service = &iface.service_name;
    let stem = module_stem(iface);
    let client = client_class_name(iface);
    let models = referenced_models(iface);

    let mut s = String::new();
    let _ = write!(
        s,
        "# @generated by jumpstarter-codegen (PythonGenerator). DO NOT EDIT.\n\
         #\n\
         # Typed client for the `{service}` interface. Each method drives the session's native\n\
         # (proto-bytes) seam via the NativeDriverClient base and decodes the response into the\n\
         # native dataclass — no protobuf/pydantic surface for the caller.\n\n\
         from jumpstarter.client.native import NativeDriverClient\n"
    );
    if iface.methods.iter().any(|m| m.server_streaming) {
        s.push_str("from collections.abc import Iterator\n");
    }
    let _ = writeln!(
        s,
        "\nfrom .{stem}_descriptor import FILE_DESCRIPTOR_SET, SERVICE_FULL_NAME"
    );
    if !models.is_empty() {
        let _ = writeln!(s, "from .{stem}_models import {}", models.join(", "));
    }
    let _ = write!(
        s,
        "\n\nclass {client}(NativeDriverClient):\n    \
             \"\"\"Typed client for one `{service}` driver instance (generated).\"\"\"\n\n    \
             _DESCRIPTOR_SET = FILE_DESCRIPTOR_SET\n    \
             _SERVICE_FULL_NAME = SERVICE_FULL_NAME\n"
    );
    for m in &iface.methods {
        if m.client_streaming {
            continue;
        }
        let snake = pascal_to_snake(&m.name);
        let path = method_path(iface, m);
        let params = py_params(iface, m);
        let sig_params = render_sig_params(&params);
        let arg_list = params
            .iter()
            .map(|(n, _)| n.clone())
            .collect::<Vec<_>>()
            .join(", ");
        let ret = py_return_base(iface, m);

        if m.server_streaming {
            let model = short_name(&m.output_type);
            let _ = write!(
                s,
                "\n    def {snake}(self{sig_params}) -> Iterator[{ret}]:\n        \
                     yield from self._native_server_stream(\"{path}\", {model}, [{arg_list}])\n"
            );
        } else if ret == "None" {
            let _ = write!(
                s,
                "\n    def {snake}(self{sig_params}) -> None:\n        \
                     self._native_unary(\"{path}\", None, [{arg_list}])\n"
            );
        } else {
            // Bare-message return uses the dataclass; a scalar wrapper decodes to the scalar (passed
            // as `None` for the return model, so the base returns the unwrapped `value`).
            let return_model = if wrapper_value_field(iface, &m.output_type).is_some() {
                "None".to_string()
            } else {
                short_name(&m.output_type).to_string()
            };
            let _ = write!(
                s,
                "\n    def {snake}(self{sig_params}) -> {ret}:\n        \
                     return self._native_unary(\"{path}\", {return_model}, [{arg_list}])\n"
            );
        }
    }
    s
}

/// Render a native parameter list as a Python signature suffix (`, voltage: float, channel: int`),
/// or the empty string for a no-param method.
fn render_sig_params(params: &[(String, String)]) -> String {
    let mut out = String::new();
    for (name, ty) in params {
        let _ = write!(out, ", {name}: {ty}");
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::interfaces_from_descriptor_set;

    const POWER_FDS: &[u8] = include_bytes!("../../tests/fixtures/power.fds");

    fn power_iface() -> InterfaceRef {
        interfaces_from_descriptor_set(POWER_FDS)
            .expect("walk power.fds")
            .into_iter()
            .next()
            .expect("one interface")
    }

    #[test]
    fn naming_helpers() {
        assert_eq!(pascal_to_snake("On"), "on");
        assert_eq!(pascal_to_snake("Read"), "read");
        assert_eq!(pascal_to_snake("SetVoltage"), "set_voltage");
        assert_eq!(strip_interface_suffix("PowerInterface"), "Power");
        let iface = power_iface();
        assert_eq!(module_stem(&iface), "power");
        assert_eq!(client_class_name(&iface), "PowerClient");
    }

    #[test]
    fn models_are_stdlib_dataclasses() {
        let src = render_models(&power_iface());
        // power has no repeated fields, so `field` is not imported (avoids an unused import).
        assert!(src.contains("from dataclasses import dataclass\n"), "{src}");
        assert!(src.contains("@dataclass\nclass PowerReading:"), "{src}");
        assert!(src.contains("    voltage: float = 0.0"), "{src}");
        assert!(src.contains("    current: float = 0.0"), "{src}");
        // Native dataclasses only — no pydantic BaseModel, no protobuf _pb2 imports.
        assert!(!src.contains("BaseModel"), "{src}");
        assert!(!src.contains("import pydantic"), "{src}");
        assert!(!src.contains("_pb2"), "{src}");
    }

    fn generator() -> PythonGenerator {
        PythonGenerator::new(None, POWER_FDS.to_vec())
    }

    #[test]
    fn driver_base_is_proto_first_abc() {
        let files = generator().generate_driver(&power_iface());
        assert!(files.contains_key("power_models.py"), "keys: {:?}", files.keys());
        assert!(files.contains_key("power_driver.py"), "keys: {:?}", files.keys());
        let src = &files["power_driver.py"];
        assert!(src.contains("class PowerInterface(ProtoInterface):"), "{src}");
        assert!(src.contains("from jumpstarter.driver import ProtoInterface"), "{src}");
        assert!(src.contains("def client(cls) -> str:"), "{src}");
        assert!(src.contains("return \"power_client.PowerClient\""), "{src}");
        assert!(src.contains("    @abstractmethod\n    async def on(self) -> None: ..."), "{src}");
        assert!(src.contains("async def read(self) -> AsyncIterator[PowerReading]: ..."), "{src}");
        assert!(src.contains("from .power_models import PowerReading"), "{src}");
    }

    #[test]
    fn client_uses_native_seam_and_native_types() {
        let files = generator().generate_client(&power_iface());
        assert!(files.contains_key("power_client.py"), "keys: {:?}", files.keys());
        let src = &files["power_client.py"];
        assert!(src.contains("class PowerClient(NativeDriverClient):"), "{src}");
        assert!(src.contains("_DESCRIPTOR_SET = FILE_DESCRIPTOR_SET"), "{src}");
        assert!(src.contains("_SERVICE_FULL_NAME = SERVICE_FULL_NAME"), "{src}");
        assert!(src.contains("from .power_descriptor import FILE_DESCRIPTOR_SET, SERVICE_FULL_NAME"), "{src}");
        assert!(src.contains("def on(self) -> None:"), "{src}");
        assert!(
            src.contains(
                "self._native_unary(\"/jumpstarter.interfaces.power.v1.PowerInterface/On\", None, [])"
            ),
            "{src}"
        );
        assert!(src.contains("def read(self) -> Iterator[PowerReading]:"), "{src}");
        assert!(
            src.contains(
                "self._native_server_stream(\"/jumpstarter.interfaces.power.v1.PowerInterface/Read\", PowerReading, [])"
            ),
            "{src}"
        );
    }

    #[test]
    fn descriptor_module_embeds_the_set_and_service() {
        let files = generator().generate_client(&power_iface());
        let src = files.get("power_descriptor.py").expect("descriptor module emitted");
        assert!(
            src.contains("SERVICE_FULL_NAME = \"jumpstarter.interfaces.power.v1.PowerInterface\""),
            "{src}"
        );
        assert!(src.contains("FILE_DESCRIPTOR_SET: bytes = bytes.fromhex("), "{src}");
        // Round-trip: the embedded hex is exactly the input descriptor set.
        let hex: String = src
            .lines()
            .filter_map(|l| l.trim().strip_prefix('"').and_then(|l| l.strip_suffix('"')))
            .collect();
        let decoded: Vec<u8> = (0..hex.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap())
            .collect();
        assert_eq!(decoded, POWER_FDS, "embedded set must round-trip byte-identical");
        // The driver kind ships it too (same file, either kind is standalone).
        assert!(generator().generate_driver(&power_iface()).contains_key("power_descriptor.py"));
    }

    #[test]
    fn package_prefix_qualifies_the_client_path() {
        let gen = PythonGenerator::new(
            Some("jumpstarter_driver_power._generated".to_string()),
            POWER_FDS.to_vec(),
        );
        let files = gen.generate_driver(&power_iface());
        assert!(
            files["power_driver.py"]
                .contains("return \"jumpstarter_driver_power._generated.power_client.PowerClient\""),
            "{}",
            files["power_driver.py"]
        );
    }
}
