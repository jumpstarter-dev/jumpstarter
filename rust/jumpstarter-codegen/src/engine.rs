//! The descriptor-walking half of the codegen engine.
//!
//! Ported from the jep-14 Python `engine.py` (`_extract_methods` / `_extract_messages`
//! / `_extract_enums`), dropping the CRD / YAML / cluster resolution. Where `engine.py`
//! walked a single `FileDescriptorProto` with raw path constants, this walks a
//! [`DescriptorPool`] built from the serialized `FileDescriptorSet` — decoded **exactly**
//! like `jumpstarter-core`'s `driver.rs::build_native_backend`
//! (`FileDescriptorSet::decode` → `DescriptorPool::from_file_descriptor_set`) — and uses
//! the resolved descriptor API (`ServiceDescriptor` / `MethodDescriptor` /
//! `MessageDescriptor` / `FieldDescriptor`) so fully-qualified type names are already
//! resolved by the pool.
//!
//! The entry point is [`interfaces_from_descriptor_set`]: one [`crate::ir::InterfaceRef`]
//! per gRPC service in the set, carrying its methods plus the messages and enums declared
//! in the same proto file.

use prost::Message as _;
use prost_reflect::prost_types::FileDescriptorSet;
use prost_reflect::{
    DescriptorPool, EnumDescriptor, FieldDescriptor, FileDescriptor, Kind, MessageDescriptor,
    ServiceDescriptor,
};

use crate::ir::{EnumType, Field, InterfaceRef, Method, MessageType};

/// Decode a serialized [`FileDescriptorSet`] and walk every gRPC service into a
/// language-neutral [`InterfaceRef`].
///
/// The set is decoded and resolved the same way the exporter resolves a native driver's
/// descriptor (`driver.rs::build_native_backend`): `FileDescriptorSet::decode` followed by
/// `DescriptorPool::from_file_descriptor_set`. Each service becomes one `InterfaceRef`; its
/// `messages`/`enums` are the message and enum types declared in the **same file** as the
/// service (top-level and, for enums, nested), so a generator has the request/response and
/// data-model types alongside the RPCs. Fully-qualified type names on methods and fields are
/// taken from the resolved descriptors (no leading dot).
pub fn interfaces_from_descriptor_set(fds_bytes: &[u8]) -> anyhow::Result<Vec<InterfaceRef>> {
    let set = FileDescriptorSet::decode(fds_bytes)
        .map_err(|e| anyhow::anyhow!("decode FileDescriptorSet: {e}"))?;
    let pool = DescriptorPool::from_file_descriptor_set(set)
        .map_err(|e| anyhow::anyhow!("build DescriptorPool (unresolved import?): {e}"))?;

    let mut interfaces = Vec::new();
    for service in pool.services() {
        interfaces.push(interface_from_service(&service));
    }
    Ok(interfaces)
}

/// Build an [`InterfaceRef`] from a resolved [`ServiceDescriptor`].
fn interface_from_service(service: &ServiceDescriptor) -> InterfaceRef {
    let file = service.parent_file();

    let methods = service.methods().map(method_from_descriptor).collect();
    let messages = file.messages().map(message_from_descriptor).collect();
    let enums = enums_from_file(&file);

    InterfaceRef {
        proto_package: service.package_name().to_string(),
        service_name: service.name().to_string(),
        doc_comment: doc_comment(&file, service.path()),
        methods,
        messages,
        enums,
    }
}

/// Map a resolved [`MethodDescriptor`] to an IR [`Method`], including the server/client
/// streaming flags and the fully-qualified (already dot-free) input/output type names.
fn method_from_descriptor(method: prost_reflect::MethodDescriptor) -> Method {
    let file = method.parent_file();
    Method {
        name: method.name().to_string(),
        input_type: method.input().full_name().to_string(),
        output_type: method.output().full_name().to_string(),
        client_streaming: method.is_client_streaming(),
        server_streaming: method.is_server_streaming(),
        doc_comment: doc_comment(&file, method.path()),
    }
}

/// Map a resolved [`MessageDescriptor`] to an IR [`MessageType`] with its fields in
/// declared order.
fn message_from_descriptor(message: MessageDescriptor) -> MessageType {
    let fields = message.fields().map(field_from_descriptor).collect();
    MessageType {
        name: message.name().to_string(),
        full_name: message.full_name().to_string(),
        fields,
    }
}

/// Map a resolved [`FieldDescriptor`] to an IR [`Field`]. Scalar fields carry the proto
/// scalar name (`"double"`, `"string"`, …); message/enum fields carry the referenced
/// type's fully-qualified name.
fn field_from_descriptor(field: FieldDescriptor) -> Field {
    let kind = field.kind();
    let (type_name, is_message, is_enum) = match &kind {
        Kind::Message(m) => (m.full_name().to_string(), true, false),
        Kind::Enum(e) => (e.full_name().to_string(), false, true),
        scalar => (scalar_type_name(scalar).to_string(), false, false),
    };
    Field {
        name: field.name().to_string(),
        number: field.number() as i32,
        type_name,
        // `is_list` is the resolved equivalent of LABEL_REPEATED (and excludes maps, which
        // report separately); the IR's `repeated` mirrors the jep-14 `is_repeated`.
        repeated: field.is_list(),
        // `supports_presence` is true for proto3 `optional` (and message-typed) fields —
        // the resolved analogue of the `proto3_optional` flag jep-14 read.
        optional: field.supports_presence(),
        is_message,
        is_enum,
    }
}

/// All enums declared in a file: top-level enums plus enums nested inside the file's
/// messages (one level, mirroring the jep-14 `_extract_enums` which collected top-level
/// enums and each message's nested enums). Fully-qualified names come from the pool.
fn enums_from_file(file: &FileDescriptor) -> Vec<EnumType> {
    let mut enums: Vec<EnumType> = file.enums().map(enum_from_descriptor).collect();
    for message in file.messages() {
        for nested in message.child_enums() {
            enums.push(enum_from_descriptor(nested));
        }
    }
    enums
}

/// Map a resolved [`EnumDescriptor`] to an IR [`EnumType`] with its `(name, number)` value
/// pairs in declared order.
fn enum_from_descriptor(enum_desc: EnumDescriptor) -> EnumType {
    let values = enum_desc
        .values()
        .map(|v| (v.name().to_string(), v.number()))
        .collect();
    EnumType {
        name: enum_desc.name().to_string(),
        full_name: enum_desc.full_name().to_string(),
        values,
    }
}

/// The proto scalar type name for a non-message/non-enum [`Kind`] — mirrors jep-14's
/// `_SCALAR_TYPE_NAMES` table. Message/enum kinds are handled by the caller (they carry a
/// fully-qualified type name instead).
fn scalar_type_name(kind: &Kind) -> &'static str {
    match kind {
        Kind::Double => "double",
        Kind::Float => "float",
        Kind::Int32 => "int32",
        Kind::Int64 => "int64",
        Kind::Uint32 => "uint32",
        Kind::Uint64 => "uint64",
        Kind::Sint32 => "sint32",
        Kind::Sint64 => "sint64",
        Kind::Fixed32 => "fixed32",
        Kind::Fixed64 => "fixed64",
        Kind::Sfixed32 => "sfixed32",
        Kind::Sfixed64 => "sfixed64",
        Kind::Bool => "bool",
        Kind::String => "string",
        Kind::Bytes => "bytes",
        // Message/Enum are resolved to full names by the caller; reaching here would be a
        // logic error, but return a stable placeholder rather than panic.
        Kind::Message(_) | Kind::Enum(_) => "unknown",
    }
}

/// Look up the leading doc comment for `path` in the file's `source_code_info` — the
/// resolved-descriptor analogue of jep-14's `_get_comment`. Trailing newlines are trimmed;
/// an empty comment is reported as `None`.
fn doc_comment(file: &FileDescriptor, path: &[i32]) -> Option<String> {
    let info = file.file_descriptor_proto().source_code_info.as_ref()?;
    for loc in &info.location {
        if loc.path == path {
            let leading = loc.leading_comments();
            let text = leading.trim_end_matches('\n');
            if !text.is_empty() {
                return Some(text.to_string());
            }
            return None;
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The committed fixture: a `FileDescriptorSet` (with imports) for the power interface
    /// proto, produced by `protoc --include_imports` (see the crate's README / structured
    /// notes for the exact command).
    const POWER_FDS: &[u8] = include_bytes!("../tests/fixtures/power.fds");

    #[test]
    fn power_descriptor_set_yields_power_interface() {
        let interfaces = interfaces_from_descriptor_set(POWER_FDS).expect("walk power.fds");

        // Exactly one service: PowerInterface.
        assert_eq!(interfaces.len(), 1, "expected a single service");
        let power = &interfaces[0];
        assert_eq!(power.service_name, "PowerInterface");
        assert_eq!(power.proto_package, "jumpstarter.interfaces.power.v1");
    }

    #[test]
    fn power_interface_methods_on_off_read_with_read_server_streaming() {
        let interfaces = interfaces_from_descriptor_set(POWER_FDS).expect("walk power.fds");
        let power = &interfaces[0];

        let names: Vec<&str> = power.methods.iter().map(|m| m.name.as_str()).collect();
        assert!(names.contains(&"On"), "On method present: {names:?}");
        assert!(names.contains(&"Off"), "Off method present: {names:?}");
        assert!(names.contains(&"Read"), "Read method present: {names:?}");

        let read = power
            .methods
            .iter()
            .find(|m| m.name == "Read")
            .expect("Read method");
        assert!(read.server_streaming, "Read must be server-streaming");
        assert!(!read.client_streaming, "Read is not client-streaming");
        assert_eq!(read.output_type, "jumpstarter.interfaces.power.v1.PowerReading");

        // On/Off are unary Empty -> Empty.
        let on = power.methods.iter().find(|m| m.name == "On").unwrap();
        assert!(!on.server_streaming && !on.client_streaming);
        assert_eq!(on.input_type, "google.protobuf.Empty");
        assert_eq!(on.output_type, "google.protobuf.Empty");
    }

    #[test]
    fn power_reading_message_has_voltage_and_current() {
        let interfaces = interfaces_from_descriptor_set(POWER_FDS).expect("walk power.fds");
        let power = &interfaces[0];

        let reading = power
            .messages
            .iter()
            .find(|m| m.name == "PowerReading")
            .expect("PowerReading message present");
        assert_eq!(
            reading.full_name,
            "jumpstarter.interfaces.power.v1.PowerReading"
        );
        let field_names: Vec<&str> = reading.fields.iter().map(|f| f.name.as_str()).collect();
        assert_eq!(field_names, vec!["voltage", "current"]);
        for f in &reading.fields {
            assert_eq!(f.type_name, "double", "field {} is a double", f.name);
            assert!(!f.is_message && !f.is_enum && !f.repeated);
        }
    }
}
