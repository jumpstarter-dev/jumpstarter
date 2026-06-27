//! Language-neutral intermediate representation for codegen.
//!
//! Ported from the jep-14 Python codegen models
//! (`jumpstarter_cli.codegen.models`), dropping the CRD/cluster/ExporterClass
//! resolution concepts (`DriverInterfaceRef.interface_ref`, `Optionality`,
//! `DriverImplementationHint`, `ExporterClassSpec`, `CodegenContext`). What remains
//! is purely the **proto contract** a per-language generator needs: one service
//! ([`InterfaceRef`]) with its [`Method`]s, plus the [`MessageType`]s and
//! [`EnumType`]s declared in the same file.
//!
//! These are plain, serde-free structs — the engine builds them by walking a
//! `FileDescriptorSet`, and the generators read them to string-build source.

/// A resolved Jumpstarter driver interface: one gRPC service plus the message and
/// enum types declared alongside it in the same proto file.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InterfaceRef {
    /// Proto package (e.g. `"jumpstarter.interfaces.power.v1"`).
    pub proto_package: String,
    /// gRPC service name (e.g. `"PowerInterface"`).
    pub service_name: String,
    /// Service-level leading doc comment from the `.proto` file, if any.
    pub doc_comment: Option<String>,
    /// The RPC methods declared on the service, in declared order.
    pub methods: Vec<Method>,
    /// Message types declared in the same file (request/response wrappers and data
    /// models alike), in declared order.
    pub messages: Vec<MessageType>,
    /// Enum types declared in the same file (top-level and nested), in declared order.
    pub enums: Vec<EnumType>,
}

/// A single RPC method on an [`InterfaceRef`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Method {
    /// Proto method name (PascalCase, e.g. `"On"`, `"Read"`).
    pub name: String,
    /// Fully-qualified proto input type (leading dot stripped, e.g.
    /// `"google.protobuf.Empty"`).
    pub input_type: String,
    /// Fully-qualified proto output type (leading dot stripped).
    pub output_type: String,
    /// True for a client-streaming method (the request is a stream).
    pub client_streaming: bool,
    /// True for a server-streaming method (the response is a stream, e.g. `Read`).
    pub server_streaming: bool,
    /// Leading doc comment from the `.proto` file, if any.
    pub doc_comment: Option<String>,
}

/// A proto message definition.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MessageType {
    /// Short name (e.g. `"PowerReading"`).
    pub name: String,
    /// Fully-qualified name (e.g. `"jumpstarter.interfaces.power.v1.PowerReading"`).
    pub full_name: String,
    /// The message's fields, in declared order.
    pub fields: Vec<Field>,
}

/// A field within a proto [`MessageType`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Field {
    /// Field name as declared in the proto.
    pub name: String,
    /// Field number.
    pub number: i32,
    /// Scalar type name (e.g. `"double"`, `"string"`) or, for message/enum fields,
    /// the fully-qualified type name (leading dot stripped).
    pub type_name: String,
    /// True if the field is `repeated`.
    pub repeated: bool,
    /// True if the field is a proto3 `optional`.
    pub optional: bool,
    /// True if the field's type is a message.
    pub is_message: bool,
    /// True if the field's type is an enum.
    pub is_enum: bool,
}

/// A proto enum definition (top-level or nested in a message).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EnumType {
    /// Short name (e.g. `"PowerState"`).
    pub name: String,
    /// Fully-qualified name (e.g. `"jumpstarter.interfaces.power.v1.PowerState"`).
    pub full_name: String,
    /// `(name, number)` value pairs, in declared order.
    pub values: Vec<(String, i32)>,
}
