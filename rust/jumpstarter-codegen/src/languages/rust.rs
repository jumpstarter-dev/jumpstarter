//! Rust language generator.
//!
//! Emits, from one [`InterfaceRef`], the two pieces of Jumpstarter-specific glue a
//! proto-first Rust driver needs — everything else (the `tonic` service trait, the prost
//! message types, and the `FILE_DESCRIPTOR_SET`) is stock `tonic-build` output the author's
//! crate compiles from the same `.proto`:
//!
//! `generate_driver` emits a `PowerBackend<T: PowerInterface>` that adapts the author's typed
//! `tonic` service impl to the core's opaque
//! [`DriverBackend`](jumpstarter_transport::DriverBackend) seam: `get_report` advertises the
//! `FILE_DESCRIPTOR_SET` (mirroring `NativeDriverBackend::new`'s report: the `jumpstarter.dev/client`
//! and `jumpstarter.dev/name` labels, `parent_uuid: None`); `forward_unary` decodes the prost
//! request, dispatches the matching typed trait method, and encodes the prost response;
//! `forward_stream` does the same for server-streaming methods; `open_router_stream`/`log_stream`
//! decline like `NativeDriverBackend`.
//!
//! `generate_client` emits a `PowerClient` over
//! [`ClientSession`](jumpstarter_client::ClientSession) (NOT a tonic stub + interceptor): `new`
//! resolves the driver instance uuid from `session.get_report()` by the `jumpstarter.dev/name`
//! label, and each typed method encodes the prost request, drives
//! `ClientSession::native_unary` / `native_server_stream`, and decodes the prost response.
//!
//! The generator is fully IR-driven: method names map to the gRPC path
//! `/<proto_package>.<Service>/<Method>`, PascalCase method names become snake_case Rust fns, and
//! the prost request/response Rust types are resolved from the proto input/output type names.

use std::collections::BTreeMap;
use std::fmt::Write as _;

use crate::ir::{InterfaceRef, Method};
use crate::languages::LanguageGenerator;

/// The Rust per-language generator.
#[derive(Debug, Default, Clone, Copy)]
pub struct RustGenerator;

impl LanguageGenerator for RustGenerator {
    fn name(&self) -> &str {
        "rust"
    }

    fn generate_driver(&self, iface: &InterfaceRef) -> BTreeMap<String, String> {
        // A proto-first driver implements the stock `tonic` service trait directly; the generator's
        // contribution is a *stub implementation* of that trait (every method `todo!()`) that the
        // author renames and fills in, then serves via `jumpstarter_driver_runtime::serve_driver`.
        // No runtime adapter is generated (that machinery is the generic runtime). (`render_driver`
        // below is the retired per-interface adapter, kept dead-allowed pending removal.)
        let mut out = BTreeMap::new();
        out.insert(driver_stub_file_name(iface), render_driver_stub(iface));
        out
    }

    fn generate_client(&self, iface: &InterfaceRef) -> BTreeMap<String, String> {
        let mut out = BTreeMap::new();
        out.insert(client_file_name(iface), render_client(iface));
        out
    }
}

// --------------------------------------------------------------------------------------------
// Naming helpers

/// `PascalCase`/`camelCase` -> `snake_case` (`On` -> `on`, `SetVoltage` -> `set_voltage`,
/// `PowerInterface` -> `power_interface`).
fn pascal_to_snake(name: &str) -> String {
    let mut out = String::with_capacity(name.len() + 4);
    let chars: Vec<char> = name.chars().collect();
    for (i, &c) in chars.iter().enumerate() {
        if c.is_ascii_uppercase() {
            let prev_lower_or_digit = i > 0 && (chars[i - 1].is_ascii_lowercase() || chars[i - 1].is_ascii_digit());
            let next_lower = i + 1 < chars.len() && chars[i + 1].is_ascii_lowercase();
            let prev_upper = i > 0 && chars[i - 1].is_ascii_uppercase();
            if i > 0 && (prev_lower_or_digit || (prev_upper && next_lower)) {
                out.push('_');
            }
            out.push(c.to_ascii_lowercase());
        } else {
            out.push(c);
        }
    }
    out
}

/// The Rust struct name for a service's backend adapter: `PowerInterface` -> `PowerBackend`
/// (else `<Service>Backend`). Retained only for the unused `render_driver`; see `generate_driver`.
#[allow(dead_code)]
fn backend_struct_name(iface: &InterfaceRef) -> String {
    format!("{}Backend", strip_interface_suffix(&iface.service_name))
}

/// The Rust struct name for a service's typed client: `PowerInterface` -> `PowerClient`
/// (else `<Service>Client`).
fn client_struct_name(iface: &InterfaceRef) -> String {
    format!("{}Client", strip_interface_suffix(&iface.service_name))
}

/// Strip a trailing `Interface` from a service name (`PowerInterface` -> `Power`); leave names
/// without that suffix unchanged.
fn strip_interface_suffix(service: &str) -> String {
    service
        .strip_suffix("Interface")
        .unwrap_or(service)
        .to_string()
}

#[allow(dead_code)]
fn driver_file_name(iface: &InterfaceRef) -> String {
    format!(
        "{}_backend.rs",
        pascal_to_snake(&strip_interface_suffix(&iface.service_name))
    )
}

fn client_file_name(iface: &InterfaceRef) -> String {
    format!(
        "{}_client.rs",
        pascal_to_snake(&strip_interface_suffix(&iface.service_name))
    )
}

/// The full gRPC method path for a method: `/<proto_package>.<Service>/<Method>`.
fn method_path(iface: &InterfaceRef, method: &Method) -> String {
    format!(
        "/{}.{}/{}",
        iface.proto_package, iface.service_name, method.name
    )
}

/// Whether a fully-qualified proto type is `google.protobuf.Empty` — `tonic-build` maps that to the
/// unit type `()`, so the generated adapter/client treat it specially (empty wire bytes).
fn is_empty_type(proto_type: &str) -> bool {
    proto_type == "google.protobuf.Empty"
}

/// The Rust type (relative to the generated module's `proto` import) for a fully-qualified proto
/// type name. `google.protobuf.Empty` maps to `()` — exactly as `tonic-build` types it in the
/// service trait — and an interface-local message maps to `proto::<Name>`. `()` and every prost
/// message both implement [`prost::Message`], so the same encode/decode applies uniformly.
fn rust_message_type(proto_type: &str) -> String {
    if is_empty_type(proto_type) {
        return "()".to_string();
    }
    let short = proto_type.rsplit('.').next().unwrap_or(proto_type);
    format!("proto::{short}")
}

// --------------------------------------------------------------------------------------------
// Driver stub generation — the author-facing skeleton (the one driver-side artifact emitted).

/// The file name for the generated driver stub: `power_driver_stub.rs`.
fn driver_stub_file_name(iface: &InterfaceRef) -> String {
    format!(
        "{}_driver_stub.rs",
        pascal_to_snake(&strip_interface_suffix(&iface.service_name))
    )
}

/// Render a stub implementation of the interface's stock `tonic` service trait — every method a
/// `todo!()` — for the author to rename and fill in. Mirrors the shape of a real driver impl; once
/// complete, the generic `jumpstarter_driver_runtime::serve_driver` serves it.
fn render_driver_stub(iface: &InterfaceRef) -> String {
    let service = &iface.service_name;
    let server_mod = format!("{}_server", pascal_to_snake(service));
    let driver = format!("{}Driver", strip_interface_suffix(service));
    let uses_proto = iface
        .methods
        .iter()
        .any(|m| !is_empty_type(&m.input_type) || !is_empty_type(&m.output_type));

    let mut out = String::new();
    let _ = write!(
        out,
        "// Stub implementation of `{service}` — generated by jumpstarter-codegen. Rename `{driver}`,\n\
         // replace each `todo!()` with your logic, then serve it with the generic runtime:\n\
         //\n\
         //   jumpstarter_driver_runtime::serve_driver(\n\
         //       \"<instance-name>\", \"<client.class>\", crate::proto::FILE_DESCRIPTOR_SET.to_vec(),\n\
         //       crate::proto::{server_mod}::{service}Server::new({driver}::default()),\n\
         //   ).await\n\n"
    );
    if uses_proto {
        out.push_str("use crate::proto;\n");
    }
    let _ = write!(
        out,
        "use crate::proto::{server_mod}::{service};\n\
         use tonic::{{Request, Response, Status}};\n\n\
         #[derive(Default)]\n\
         pub struct {driver};\n\n\
         #[tonic::async_trait]\n\
         impl {service} for {driver} {{\n"
    );
    for m in &iface.methods {
        let snake = pascal_to_snake(&m.name);
        let in_ty = rust_message_type(&m.input_type);
        let out_ty = rust_message_type(&m.output_type);
        if m.server_streaming {
            let stream_ty = format!("{}Stream", m.name);
            let _ = write!(
                out,
                "    type {stream_ty} =\n        std::pin::Pin<Box<dyn tokio_stream::Stream<Item = Result<{out_ty}, Status>> + Send>>;\n\n    async fn {snake}(&self, _request: Request<{in_ty}>) -> Result<Response<Self::{stream_ty}>, Status> {{\n        todo!(\"implement {service}::{snake}\")\n    }}\n"
            );
        } else {
            let _ = write!(
                out,
                "    async fn {snake}(&self, _request: Request<{in_ty}>) -> Result<Response<{out_ty}>, Status> {{\n        todo!(\"implement {service}::{snake}\")\n    }}\n"
            );
        }
    }
    out.push_str("}\n");
    out
}

// --------------------------------------------------------------------------------------------
// Driver (server-side adapter) generation — UNUSED. Proto-first drivers are the stock tonic
// service served by `jumpstarter_driver_runtime::serve_driver`; nothing here is generated. Kept
// only until `LanguageGenerator::generate_driver` is removed (pending the Java migration).

#[allow(dead_code)]
fn render_driver(iface: &InterfaceRef) -> String {
    let service = &iface.service_name;
    let backend = backend_struct_name(iface);
    let snake_service = pascal_to_snake(service);
    let server_mod = format!("{snake_service}_server");
    let trait_name = service.clone();

    let unary: Vec<&Method> = iface
        .methods
        .iter()
        .filter(|m| !m.server_streaming && !m.client_streaming)
        .collect();
    let server_stream: Vec<&Method> = iface
        .methods
        .iter()
        .filter(|m| m.server_streaming && !m.client_streaming)
        .collect();

    let mut s = String::new();
    let _ = writeln!(
        s,
        "// @generated by jumpstarter-codegen (RustGenerator). DO NOT EDIT.\n\
         //\n\
         // Driver adapter for the `{service}` interface: bridges the author's typed `tonic`\n\
         // service impl (`impl {trait_name}`) to the core's opaque `DriverBackend` seam.\n\
         //\n\
         // The `proto` module (the `tonic`/`prost` output + `FILE_DESCRIPTOR_SET`) is provided by\n\
         // the embedding crate's `build.rs`; this file `use`s it via `crate::proto`.\n"
    );

    let _ = writeln!(
        s,
        "use std::collections::HashMap;\n\
         use std::sync::Arc;\n\n\
         use bytes::Bytes;\n\
         use jumpstarter_protocol::v1::{{DriverInstanceReport, GetReportResponse, LogStreamResponse}};\n\
         use jumpstarter_transport::{{\n    \
             DriverBackend, FrameUplink, ResponseStream, RouterStreamOpen,\n\
         }};\n\
         use tokio_stream::StreamExt as _;\n\
         use tonic::metadata::{{AsciiMetadataValue, MetadataMap}};\n\
         use tonic::{{Request, Status}};\n\n\
         use crate::proto;\n\
         use crate::proto::{server_mod}::{trait_name};\n"
    );

    let _ = writeln!(
        s,
        "const CLIENT_LABEL: &str = \"jumpstarter.dev/client\";\n\
         const NAME_LABEL: &str = \"jumpstarter.dev/name\";\n"
    );

    // Struct + constructor (report mirrors NativeDriverBackend::new).
    let _ = writeln!(
        s,
        "/// Serves a typed `{trait_name}` implementation over the proto `DriverBackend` seam: the\n\
         /// generated counterpart of `NativeDriverBackend`, but dispatching to a native `tonic`\n\
         /// service impl (decode prost -> typed method -> encode prost) rather than the JSON\n\
         /// `Driver::call` path. The driver is the host's root entry (`parent_uuid: None`).\n\
         pub struct {backend}<T: {trait_name}> {{\n    \
             inner: Arc<T>,\n    \
             report: GetReportResponse,\n\
         }}\n"
    );

    let _ = writeln!(
        s,
        "impl<T: {trait_name}> {backend}<T> {{\n    \
             /// Serve `inner` as the top-level entry named `entry_name`, advertising the\n    \
             /// interface descriptor (`FILE_DESCRIPTOR_SET`) so a client can decode it and drive\n    \
             /// this driver over the native wire. `client_class` is the `jumpstarter.dev/client`\n    \
             /// label (the client class that drives this driver).\n    \
             pub fn new(entry_name: &str, client_class: &str, inner: T) -> Self {{\n        \
                 Self::from_arc(entry_name, client_class, Arc::new(inner))\n    \
             }}\n\n    \
             /// As [`new`](Self::new) but takes a shared `Arc<T>` (so the caller can keep a handle\n    \
             /// on the driver — e.g. to assert on its state in a test).\n    \
             pub fn from_arc(entry_name: &str, client_class: &str, inner: Arc<T>) -> Self {{\n        \
                 let uuid = uuid_new_v4();\n        \
                 let reports = vec![DriverInstanceReport {{\n            \
                     uuid,\n            \
                     parent_uuid: None,\n            \
                     labels: HashMap::from([\n                \
                         (CLIENT_LABEL.to_string(), client_class.to_string()),\n                \
                         (NAME_LABEL.to_string(), entry_name.to_string()),\n            \
                     ]),\n            \
                     description: None,\n            \
                     methods_description: HashMap::new(),\n            \
                     descriptor_set: Some(proto::FILE_DESCRIPTOR_SET.to_vec()),\n        \
                 }}];\n        \
                 Self {{\n            \
                     inner,\n            \
                     report: GetReportResponse {{\n                \
                         reports,\n                \
                         ..Default::default()\n            \
                     }},\n        \
                 }}\n    \
             }}\n    \n    \
             /// The driver instance uuid this backend advertises (its single report entry).\n    \
             pub fn uuid(&self) -> &str {{\n        \
                 &self.report.reports[0].uuid\n    \
             }}\n\
         }}\n"
    );

    // A tiny local uuid v4 generator so the driver crate need not depend on the `uuid` crate.
    let _ = writeln!(
        s,
        "/// A random RFC-4122 v4 uuid string, built from `std`-only randomness (the generated\n\
         /// driver crate need not depend on the `uuid` crate just to mint an instance id).\n\
         fn uuid_new_v4() -> String {{\n    \
             use std::hash::{{Hash, Hasher}};\n    \
             let mut hasher = std::collections::hash_map::DefaultHasher::new();\n    \
             std::time::SystemTime::now().hash(&mut hasher);\n    \
             std::thread::current().id().hash(&mut hasher);\n    \
             let hi = hasher.finish();\n    \
             std::ptr::addr_of!(hasher).hash(&mut hasher);\n    \
             let lo = hasher.finish();\n    \
             let b = |n: u64, shift: u32| ((n >> shift) & 0xff) as u8;\n    \
             let mut bytes = [\n        \
                 b(hi, 56), b(hi, 48), b(hi, 40), b(hi, 32),\n        \
                 b(hi, 24), b(hi, 16), b(hi, 8), b(hi, 0),\n        \
                 b(lo, 56), b(lo, 48), b(lo, 40), b(lo, 32),\n        \
                 b(lo, 24), b(lo, 16), b(lo, 8), b(lo, 0),\n    \
             ];\n    \
             bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4\n    \
             bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant\n    \
             format!(\n        \
                 \"{{:02x}}{{:02x}}{{:02x}}{{:02x}}-{{:02x}}{{:02x}}-{{:02x}}{{:02x}}-{{:02x}}{{:02x}}-{{:02x}}{{:02x}}{{:02x}}{{:02x}}{{:02x}}{{:02x}}\",\n        \
                 bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],\n        \
                 bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15],\n    \
             )\n\
         }}\n"
    );

    // DriverBackend impl.
    let _ = writeln!(
        s,
        "#[tonic::async_trait]\n\
         impl<T: {trait_name}> DriverBackend for {backend}<T> {{\n    \
             async fn get_report(&self) -> Result<GetReportResponse, Status> {{\n        \
                 Ok(self.report.clone())\n    \
             }}\n"
    );

    // forward_unary.
    s.push_str(
        "    async fn forward_unary(\n        \
             &self,\n        \
             path: &str,\n        \
             _metadata: MetadataMap,\n        \
             body: Bytes,\n    \
         ) -> Result<(MetadataMap, Bytes, MetadataMap), Status> {\n        \
             match path {\n",
    );
    for m in &unary {
        let path = method_path(iface, m);
        let snake = pascal_to_snake(&m.name);
        let in_ty = rust_message_type(&m.input_type);
        let _ = writeln!(
            s,
            "            \"{path}\" => {{\n                \
                 let req = <{in_ty} as prost::Message>::decode(body)\n                    \
                     .map_err(|e| Status::invalid_argument(format!(\"decode {name}: {{e}}\")))?;\n                \
                 let resp = self.inner.{snake}(Request::new(req)).await?.into_inner();\n                \
                 let bytes = Bytes::from(prost::Message::encode_to_vec(&resp));\n                \
                 Ok((MetadataMap::new(), bytes, MetadataMap::new()))\n            \
             }}",
            name = m.name,
        );
    }
    s.push_str(
        "            other => Err(Status::unimplemented(format!(\n                \
                 \"no unary method {other} on this backend\"\n            \
             ))),\n        \
         }\n    \
         }\n",
    );

    // forward_stream (server-streaming methods, else defer to forward_unary's one-item stream).
    s.push_str(
        "\n    async fn forward_stream(\n        \
             &self,\n        \
             path: &str,\n        \
             metadata: MetadataMap,\n        \
             body: Bytes,\n    \
         ) -> Result<(MetadataMap, ResponseStream<Bytes>), Status> {\n        \
             match path {\n",
    );
    for m in &server_stream {
        let path = method_path(iface, m);
        let snake = pascal_to_snake(&m.name);
        let in_ty = rust_message_type(&m.input_type);
        let _ = writeln!(
            s,
            "            \"{path}\" => {{\n                \
                 let req = <{in_ty} as prost::Message>::decode(body)\n                    \
                     .map_err(|e| Status::invalid_argument(format!(\"decode {name}: {{e}}\")))?;\n                \
                 let inner = self.inner.{snake}(Request::new(req)).await?.into_inner();\n                \
                 let mapped: ResponseStream<Bytes> = Box::pin(inner.map(|item| {{\n                    \
                     item.map(|msg| Bytes::from(prost::Message::encode_to_vec(&msg)))\n                \
                 }}));\n                \
                 Ok((MetadataMap::new(), mapped))\n            \
             }}",
            name = m.name,
        );
    }
    // For non-streaming methods, present the unary result as a one-item stream (the demux frames
    // every native call as a stream — a unary client reads a one-message stream identically).
    s.push_str(
        "            _ => {\n                \
             let (initial, message, _trailers) = self.forward_unary(path, metadata, body).await?;\n                \
             let stream: ResponseStream<Bytes> = Box::pin(tokio_stream::once(Ok(message)));\n                \
             Ok((initial, stream))\n            \
         }\n        \
         }\n    \
         }\n",
    );

    // Declined router/log streams (mirroring NativeDriverBackend).
    let _ = writeln!(
        s,
        "\n    async fn open_router_stream(\n        \
             &self,\n        \
             _request_meta: AsciiMetadataValue,\n        \
             _uplink: FrameUplink,\n    \
         ) -> Result<RouterStreamOpen, Status> {{\n        \
             Err(Status::unimplemented(\n            \
                 \"byte streams are not supported by this native driver\",\n        \
             ))\n    \
         }}\n\n    \
         async fn log_stream(&self) -> Result<ResponseStream<LogStreamResponse>, Status> {{\n        \
             Ok(Box::pin(tokio_stream::empty()))\n    \
         }}\n\
         }}\n"
    );

    s
}

// --------------------------------------------------------------------------------------------
// Client generation

fn render_client(iface: &InterfaceRef) -> String {
    let service = &iface.service_name;
    let client = client_struct_name(iface);

    let mut s = String::new();
    let _ = writeln!(
        s,
        "// @generated by jumpstarter-codegen (RustGenerator). DO NOT EDIT.\n\
         //\n\
         // Typed client for the `{service}` interface, driven over `ClientSession` (NOT a tonic\n\
         // stub + interceptor): each method encodes the prost request, drives the session's opaque\n\
         // native transport (`native_unary` / `native_server_stream`), and decodes the prost\n\
         // response. The `proto` module is provided by the embedding crate's `build.rs`.\n"
    );

    let _ = writeln!(
        s,
        "use jumpstarter_codec::error::DriverCallError;\n\
         use jumpstarter_client::ClientSession;\n\
         use tokio_stream::StreamExt as _;\n\n\
         use crate::proto;\n"
    );

    let _ = writeln!(
        s,
        "/// A typed client for one `{service}` driver instance. Holds a borrow of the\n\
         /// [`ClientSession`] plus the resolved driver-instance uuid; each method is a typed wrapper\n\
         /// over the session's opaque native transport.\n\
         pub struct {client}<'a> {{\n    \
             session: &'a ClientSession,\n    \
             uuid: String,\n\
         }}\n"
    );

    let _ = writeln!(
        s,
        "impl<'a> {client}<'a> {{\n    \
             /// Build a client for the driver instance named `driver_name`, resolving its uuid from\n    \
             /// the session's `GetReport` by the `jumpstarter.dev/name` label.\n    \
             pub async fn new(\n        \
                 session: &'a ClientSession,\n        \
                 driver_name: &str,\n    \
             ) -> Result<Self, DriverCallError> {{\n        \
                 let uuid = resolve_uuid(session, driver_name).await?;\n        \
                 Ok(Self {{ session, uuid }})\n    \
             }}\n\n    \
             /// Build a client for a driver instance whose uuid is already known (e.g. read from a\n    \
             /// report by the caller), skipping the name lookup.\n    \
             pub fn with_uuid(session: &'a ClientSession, uuid: String) -> Self {{\n        \
                 Self {{ session, uuid }}\n    \
             }}\n\n    \
             /// The resolved driver-instance uuid this client targets.\n    \
             pub fn uuid(&self) -> &str {{\n        \
                 &self.uuid\n    \
             }}\n"
    );

    for m in &iface.methods {
        if m.client_streaming {
            // Client-/bidi-streaming methods are not part of the power milestone; skip them rather
            // than emit a method the embedding crate could not satisfy.
            continue;
        }
        let snake = pascal_to_snake(&m.name);
        let path = method_path(iface, m);
        let in_ty = rust_message_type(&m.input_type);
        let out_ty = rust_message_type(&m.output_type);
        let in_is_empty = is_empty_type(&m.input_type);

        // Empty input -> no request param, empty wire body. A typed message -> a `request` param
        // encoded via the (uniform) `prost::Message` path.
        let (param, encode_expr) = if in_is_empty {
            ("&self".to_string(), "Vec::new()".to_string())
        } else {
            (
                format!("&self, request: {in_ty}"),
                "prost::Message::encode_to_vec(&request)".to_string(),
            )
        };

        if m.server_streaming {
            // Server-streaming: return a typed stream of decoded output messages.
            let _ = writeln!(
                s,
                "\n    /// `{name}` (server-streaming): drive the native server-streaming call and\n    \
                 /// decode each response message.\n    \
                 pub async fn {snake}(\n        {param},\n    \
                 ) -> Result<\n        \
                     impl tokio_stream::Stream<Item = Result<{out_ty}, DriverCallError>>,\n        \
                     DriverCallError,\n    \
                 > {{\n        \
                     let body = {encode_expr};\n        \
                     let stream = self\n            \
                         .session\n            \
                         .native_server_stream(self.uuid.clone(), \"{path}\".to_string(), body)\n            \
                         .await?;\n        \
                     Ok(stream.map(|item| match item {{\n            \
                         Ok(bytes) => <{out_ty} as prost::Message>::decode(bytes)\n                \
                             .map_err(|e| DriverCallError::Unknown(format!(\"decode {name}: {{e}}\"))),\n            \
                         Err(status) => Err(DriverCallError::Unknown(status.message().to_string())),\n        \
                     }}))\n    \
                 }}",
                name = m.name,
            );
        } else {
            // Unary: encode request, native_unary, decode response.
            let returns_empty = is_empty_type(&m.output_type);
            let ret_ty = if returns_empty {
                "()".to_string()
            } else {
                out_ty.clone()
            };
            let decode_block = if returns_empty {
                "        let _ = resp;\n        Ok(())".to_string()
            } else {
                format!(
                    "        let out = <{out_ty} as prost::Message>::decode(bytes::Bytes::from(resp))\n            \
                         .map_err(|e| DriverCallError::Unknown(format!(\"decode {name}: {{e}}\")))?;\n        \
                     Ok(out)",
                    name = m.name,
                )
            };
            let _ = writeln!(
                s,
                "\n    /// `{name}` (unary).\n    \
                 pub async fn {snake}({param}) -> Result<{ret_ty}, DriverCallError> {{\n        \
                     let body = {encode_expr};\n        \
                     let resp = self\n            \
                         .session\n            \
                         .native_unary(self.uuid.clone(), \"{path}\".to_string(), body)\n            \
                         .await?;\n\
                 {decode_block}\n    \
                 }}",
                name = m.name,
            );
        }
    }

    s.push_str("}\n");

    // uuid resolution from the session report JSON.
    let _ = writeln!(
        s,
        "\n/// Resolve a driver instance uuid from the session's `GetReport` by its\n\
         /// `jumpstarter.dev/name` label. The report is a JSON array of\n\
         /// `{{uuid, labels, ...}}` nodes (see `ClientSession::get_report`).\n\
         async fn resolve_uuid(\n    \
             session: &ClientSession,\n    \
             driver_name: &str,\n\
         ) -> Result<String, DriverCallError> {{\n    \
             let report_json = session.get_report().await?;\n    \
             let nodes: Vec<serde_json::Value> = serde_json::from_str(&report_json)\n        \
                 .map_err(|e| DriverCallError::Unknown(format!(\"parse report: {{e}}\")))?;\n    \
             for node in nodes {{\n        \
                 let name = node\n            \
                     .get(\"labels\")\n            \
                     .and_then(|l| l.get(\"jumpstarter.dev/name\"))\n            \
                     .and_then(|n| n.as_str());\n        \
                 if name == Some(driver_name) {{\n            \
                     if let Some(uuid) = node.get(\"uuid\").and_then(|u| u.as_str()) {{\n                \
                         return Ok(uuid.to_string());\n            \
                     }}\n        \
                 }}\n    \
             }}\n    \
             Err(DriverCallError::NotFound(format!(\n        \
                 \"driver {{driver_name:?}} not found in the device tree\"\n    \
             )))\n\
         }}\n"
    );

    // The entrypoints are NOT generated as per-interface macros — a driver crate registers its
    // interface(s) explicitly via the `jumpstarter_driver_runtime::Host` / `::Client`
    // builders and selects one at runtime (`--interface`), so one binary serves/drives any of a
    // crate's interfaces. This file provides the typed client + `proto`; the author wires the rest.
    s
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
    fn pascal_to_snake_cases() {
        assert_eq!(pascal_to_snake("On"), "on");
        assert_eq!(pascal_to_snake("Off"), "off");
        assert_eq!(pascal_to_snake("Read"), "read");
        assert_eq!(pascal_to_snake("SetVoltage"), "set_voltage");
        assert_eq!(pascal_to_snake("PowerInterface"), "power_interface");
        assert_eq!(pascal_to_snake("HTTPServer"), "http_server");
    }

    #[test]
    fn driver_stub_skeleton_generated() {
        // The driver-side artifact is a stub impl of the stock tonic service trait (todo! bodies),
        // not a runtime adapter — the generic runtime serves it once the author fills it in.
        let iface = power_iface();
        let files = RustGenerator.generate_driver(&iface);
        assert!(files.contains_key("power_driver_stub.rs"), "keys: {:?}", files.keys());
        let src = &files["power_driver_stub.rs"];
        assert!(src.contains("pub struct PowerDriver;"));
        assert!(src.contains("impl PowerInterface for PowerDriver"));
        assert!(src.contains("async fn on(&self, _request: Request<()>) -> Result<Response<()>, Status>"));
        // Server-streaming Read carries an associated stream type.
        assert!(src.contains("type ReadStream"));
        assert!(src.contains("async fn read(&self, _request: Request<()>) -> Result<Response<Self::ReadStream>, Status>"));
        assert!(src.contains("todo!(\"implement PowerInterface::on\")"));
    }

    #[test]
    fn client_output_keyed_by_client_file() {
        let iface = power_iface();
        let files = RustGenerator.generate_client(&iface);
        assert!(files.contains_key("power_client.rs"), "keys: {:?}", files.keys());
        let src = &files["power_client.rs"];
        assert!(src.contains("pub struct PowerClient<'a>"));
        // Unary on()/off() over native_unary; read() over native_server_stream.
        assert!(src.contains("pub async fn on(&self) -> Result<(), DriverCallError>"));
        assert!(src.contains("pub async fn off(&self) -> Result<(), DriverCallError>"));
        assert!(src.contains(".native_unary(self.uuid.clone()"));
        assert!(src.contains("pub async fn read("));
        assert!(src.contains(".native_server_stream(self.uuid.clone()"));
        assert!(src.contains("proto::PowerReading"));
    }
}
