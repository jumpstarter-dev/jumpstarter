//! Java/Kotlin language generator.
//!
//! Emits, from one [`InterfaceRef`], the two pieces of Jumpstarter-specific glue a proto-first
//! JVM driver needs — everything else (the grpc-java `PowerInterfaceImplBase`, the protobuf-java
//! message types, and `Power.getDescriptor()`) is stock `protoc`/`protoc-gen-grpc-java` output the
//! author's Gradle build compiles from the same `.proto`:
//!
//! - [`generate_driver`](JavaGenerator::generate_driver) — a Kotlin `PowerBackend` that adapts the
//!   author's typed grpc-java service impl (`class MockPower : PowerInterfaceImplBase()`) to the
//!   Rust core's foreign `DriverHost` seam (`jumpstarter-core-uniffi`). `describe()` advertises the
//!   interface descriptor via the reused `DescriptorSets.selfContained(Power.getDescriptor())`;
//!   `forwardUnary` decodes the protobuf request with the stock `MethodDescriptor` marshaller,
//!   dispatches the matching `ImplBase` method through a capturing `StreamObserver`, and encodes the
//!   response; `forwardServerStream`/`forwardStreamNext`/`forwardStreamClose` do the same for
//!   server-streaming methods (`Read`). The JSON `driverCall`/byte-stream surfaces decline.
//! - [`generate_client`](JavaGenerator::generate_client) — a thin Kotlin `PowerClient` over the
//!   existing `UniffiChannel` + the stock grpc-java **blocking stub** (the client side already works
//!   through `UniffiChannel`; this just emits the per-method wrapper).
//!
//! The generator is fully IR-driven: method names map to the gRPC path
//! `/<proto_package>.<Service>/<Method>`, the grpc-java method getters are `get<Method>Method()`, and
//! the Java message/outer-class names are resolved from the proto package + type names.

use std::collections::BTreeMap;
use std::fmt::Write as _;

use crate::ir::{InterfaceRef, Method};
use crate::languages::LanguageGenerator;

/// The Java/Kotlin per-language generator.
#[derive(Debug, Default, Clone, Copy)]
pub struct JavaGenerator;

impl LanguageGenerator for JavaGenerator {
    fn name(&self) -> &str {
        "java"
    }

    fn generate_driver(&self, iface: &InterfaceRef) -> BTreeMap<String, String> {
        // A proto-first JVM driver implements the stock grpc-java `…ImplBase` directly; the
        // generator's contribution is a *stub implementation* (every method `TODO()`) the author
        // renames and fills in, then serves via `GrpcServiceDriverHostFactory`. No runtime adapter
        // is generated (that machinery is the generic `GrpcServiceDriverHost`). (`render_driver`
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

/// `PascalCase`/`camelCase` -> `camelCase` (`On` -> `on`, `SetVoltage` -> `setVoltage`). grpc-java
/// method names + getter stems are lower-camel; the proto method is PascalCase.
fn pascal_to_camel(name: &str) -> String {
    let mut chars = name.chars();
    match chars.next() {
        Some(first) => first.to_ascii_lowercase().to_string() + chars.as_str(),
        None => String::new(),
    }
}

/// `snake_case` -> `PascalCase` (`storage_mux` -> `StorageMux`, `power` -> `Power`). Used to derive
/// the protobuf-java **outer class** name from the proto filename stem.
fn snake_to_pascal(name: &str) -> String {
    let mut out = String::with_capacity(name.len());
    let mut upper_next = true;
    for c in name.chars() {
        if c == '_' {
            upper_next = true;
        } else if upper_next {
            out.push(c.to_ascii_uppercase());
            upper_next = false;
        } else {
            out.push(c);
        }
    }
    out
}

/// Strip a trailing `Interface` from a service name (`PowerInterface` -> `Power`); leave names
/// without that suffix unchanged.
fn strip_interface_suffix(service: &str) -> String {
    service
        .strip_suffix("Interface")
        .unwrap_or(service)
        .to_string()
}

/// The Kotlin class name for a service's backend adapter: `PowerInterface` -> `PowerBackend`.
/// Retained only for the unused `render_driver`; see `generate_driver`.
#[allow(dead_code)]
fn backend_class_name(iface: &InterfaceRef) -> String {
    format!("{}Backend", strip_interface_suffix(&iface.service_name))
}

/// The Kotlin class name for a service's typed client: `PowerInterface` -> `PowerClient`.
fn client_class_name(iface: &InterfaceRef) -> String {
    format!("{}Client", strip_interface_suffix(&iface.service_name))
}

#[allow(dead_code)]
fn driver_file_name(iface: &InterfaceRef) -> String {
    format!("{}.kt", backend_class_name(iface))
}

fn client_file_name(iface: &InterfaceRef) -> String {
    format!("{}.kt", client_class_name(iface))
}

/// The full gRPC method path for a method: `/<proto_package>.<Service>/<Method>`.
fn method_path(iface: &InterfaceRef, method: &Method) -> String {
    format!(
        "/{}.{}/{}",
        iface.proto_package, iface.service_name, method.name
    )
}

/// The Java package for a proto package — verbatim (no `java_package` option on the interfaces),
/// matching the protoc default (`jumpstarter.interfaces.power.v1`).
fn java_package(iface: &InterfaceRef) -> &str {
    &iface.proto_package
}

/// The protobuf-java **outer class** name (e.g. `Power` for `power.proto`). The interfaces set no
/// `java_outer_classname`, so protoc derives it from the filename stem; the filename stem is the
/// package segment just before the `vN` version (`jumpstarter.interfaces.power.v1` -> `power`).
/// PascalCased: `power` -> `Power`, `storage_mux` -> `StorageMux`.
fn outer_class_name(iface: &InterfaceRef) -> String {
    let segments: Vec<&str> = iface.proto_package.split('.').collect();
    // The segment before the trailing `vN` (or the last segment if there is no version).
    let stem = segments
        .iter()
        .rev()
        .find(|s| !(s.starts_with('v') && s[1..].chars().all(|c| c.is_ascii_digit()) && s.len() > 1))
        .copied()
        .unwrap_or("");
    snake_to_pascal(stem)
}

/// The grpc-java service stub class FQN: `<java_pkg>.<Service>Grpc` (for `import` lines).
fn grpc_class(iface: &InterfaceRef) -> String {
    format!("{}.{}Grpc", java_package(iface), iface.service_name)
}

/// The grpc-java service stub **simple** class name: `<Service>Grpc` (for expression positions,
/// where the FQN is invalid because a leading package segment is not a value).
fn grpc_simple(iface: &InterfaceRef) -> String {
    format!("{}Grpc", iface.service_name)
}

/// The fully-qualified Java type for a proto message type, relative to the generated file's
/// imports. `google.protobuf.Empty` -> `com.google.protobuf.Empty`; an interface-local message
/// maps to `<outer_class>.<Name>` (a nested class of the protobuf-java outer class).
fn java_message_type(iface: &InterfaceRef, proto_type: &str) -> String {
    if proto_type == "google.protobuf.Empty" {
        return "com.google.protobuf.Empty".to_string();
    }
    let short = proto_type.rsplit('.').next().unwrap_or(proto_type);
    format!("{}.{}", outer_class_name(iface), short)
}

/// `true` for the well-known `google.protobuf.Empty`.
fn is_empty(proto_type: &str) -> bool {
    proto_type == "google.protobuf.Empty"
}

// --------------------------------------------------------------------------------------------
// Driver stub generation — the author-facing skeleton (the one driver-side artifact emitted).

/// The file name for the generated driver stub: `PowerDriver.kt`.
fn driver_stub_file_name(iface: &InterfaceRef) -> String {
    format!("{}Driver.kt", strip_interface_suffix(&iface.service_name))
}

/// Render a stub implementation of the interface's stock grpc-java `…ImplBase` — every method a
/// `TODO()` — for the author to rename and fill in. Mirrors a real driver impl; once complete, the
/// generic `GrpcServiceDriverHost` serves it.
fn render_driver_stub(iface: &InterfaceRef) -> String {
    let service = &iface.service_name;
    let pkg = java_package(iface);
    let outer = outer_class_name(iface);
    let grpc = grpc_class(iface);
    let driver = format!("{}Driver", strip_interface_suffix(service));
    let uses_message = iface
        .methods
        .iter()
        .any(|m| !is_empty(&m.input_type) || !is_empty(&m.output_type));

    let mut out = String::new();
    let _ = write!(
        out,
        "// Stub implementation of `{service}` — generated by jumpstarter-codegen. Rename `{driver}`,\n\
         // replace each `TODO()` with your logic, then serve it with the generic runtime, e.g.:\n\
         //\n\
         //   class {service}DriverHostFactory : DriverHostFactory by GrpcServiceDriverHostFactory(\n\
         //       driverName = \"<instance-name>\", clientClass = \"<client.class>\",\n\
         //       descriptorSet = DescriptorSets.selfContained({outer}.getDescriptor()),\n\
         //       service = {{ {driver}() }},\n\
         //   )\n\
         package {pkg}\n\n"
    );
    if uses_message {
        let _ = writeln!(out, "import {pkg}.{outer}");
    }
    let _ = write!(
        out,
        "import io.grpc.stub.StreamObserver\n\
         import {grpc}.{service}ImplBase\n\n\
         class {driver} : {service}ImplBase() {{\n"
    );
    for m in &iface.methods {
        let camel = pascal_to_camel(&m.name);
        let in_ty = java_message_type(iface, &m.input_type);
        let out_ty = java_message_type(iface, &m.output_type);
        let _ = write!(
            out,
            "    override fun {camel}(request: {in_ty}, responseObserver: StreamObserver<{out_ty}>) {{\n        TODO(\"implement {service}.{camel}\")\n    }}\n\n"
        );
    }
    out.push_str("}\n");
    out
}

// --------------------------------------------------------------------------------------------
// Driver (server-side adapter) generation — UNUSED. Proto-first JVM drivers are the stock
// grpc-java service served by the generic `dev.jumpstarter.driver.GrpcServiceDriverHost`; nothing
// here is generated. Kept only until `LanguageGenerator::generate_driver` is removed.

#[allow(dead_code)]
fn render_driver(iface: &InterfaceRef) -> String {
    let service = &iface.service_name;
    let backend = backend_class_name(iface);
    let pkg = java_package(iface);
    let outer = outer_class_name(iface);
    let grpc = grpc_class(iface);
    // Simple class name for expression positions (a leading package segment is not a value in
    // Kotlin, so `PowerInterfaceGrpc.getOnMethod()` must use the imported simple name).
    let grpc_x = grpc_simple(iface);
    let impl_base = format!("{grpc}.{service}ImplBase");

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
        "// @generated by jumpstarter-codegen (JavaGenerator). DO NOT EDIT.\n\
         //\n\
         // Driver adapter for the `{service}` interface: bridges the author's typed grpc-java\n\
         // service impl (`class … : {service}ImplBase()`) to the Rust core's foreign `DriverHost`\n\
         // seam. The grpc-java `{service}ImplBase`, the protobuf-java `{outer}` messages, and\n\
         // `{outer}.getDescriptor()` are stock `protoc`/grpc-java output the embedding Gradle build\n\
         // compiles from the same `.proto`.\n\
         package dev.jumpstarter.generated.{snake_pkg}\n",
        snake_pkg = generated_subpackage(iface),
    );

    let _ = writeln!(
        s,
        "import dev.jumpstarter.driver.DescriptorSets\n\
         import io.grpc.stub.StreamObserver\n\
         import {pkg}.{outer}\n\
         import {grpc}\n\
         import uniffi.jumpstarter_core.DriverException\n\
         import uniffi.jumpstarter_core.DriverHost\n\
         import uniffi.jumpstarter_core.DriverNode\n\
         import uniffi.jumpstarter_core.OpenStream\n\
         import java.util.concurrent.ConcurrentHashMap\n\
         import java.util.concurrent.atomic.AtomicLong\n"
    );

    // The backend class.
    let _ = writeln!(
        s,
        "private const val NAME_LABEL = \"jumpstarter.dev/name\"\n\n\
         /**\n \
         * Adapts an author's `{service}ImplBase` to the Rust core's [DriverHost] foreign-trait seam:\n \
         * inbound native `(path, body)` calls are decoded with the stock grpc-java `MethodDescriptor`\n \
         * marshallers, dispatched into the typed `ImplBase` methods via a capturing [StreamObserver],\n \
         * and the response re-encoded — so the author writes a plain grpc-java service and never\n \
         * touches the wire codec. The interface's native gRPC service is advertised from\n \
         * `{outer}.getDescriptor()` via [DescriptorSets.selfContained].\n \
         */\n\
         class {backend}(\n    \
             private val impl: {impl_base},\n    \
             private val driverName: String,\n\
         ) : DriverHost {{"
    );

    // Server-streaming handle registry (for forwardServerStream).
    let _ = writeln!(
        s,
        "    private val streams = ConcurrentHashMap<ULong, Iterator<ByteArray>>()\n    \
             private val nextHandle = AtomicLong(1)\n"
    );

    // describe()
    let _ = writeln!(
        s,
        "    override suspend fun describe(): List<DriverNode> = listOf(\n        \
                 DriverNode(\n            \
                     uuid = driverUuid,\n            \
                     parentUuid = null,\n            \
                     labels = mapOf(NAME_LABEL to driverName),\n            \
                     description = null,\n            \
                     methodsDescription = emptyMap(),\n            \
                     descriptorSet = DescriptorSets.selfContained({outer}.getDescriptor()),\n        \
                 ),\n    \
             )\n"
    );

    // forwardUnary(uuid, path, body)
    s.push_str(
        "    override suspend fun forwardUnary(uuid: String, path: String, body: ByteArray): ByteArray =\n        \
             when (path) {\n",
    );
    for m in &unary {
        let path = method_path(iface, m);
        let camel = pascal_to_camel(&m.name);
        let getter = format!("get{}Method", m.name);
        let _ = writeln!(
            s,
            "            \"{path}\" -> {{\n                \
                 val request = {grpc_x}.{getter}().parseRequest(body.inputStream())\n                \
                 val response = invokeUnary {{ obs -> impl.{camel}(request, obs) }}\n                \
                 {grpc_x}.{getter}().streamResponse(response).readBytes()\n            \
             }}",
        );
    }
    // Server-streaming + unknown unary paths decline → the core falls back / surfaces UNIMPLEMENTED.
    s.push_str(
        "            else -> throw DriverException.Unimplemented(\"no unary method at path '$path'\")\n        \
             }\n\n",
    );

    // forwardServerStream / forwardStreamNext / forwardStreamClose
    s.push_str(
        "    override suspend fun forwardServerStream(uuid: String, path: String, body: ByteArray): ULong {\n        \
             val messages: List<ByteArray> = when (path) {\n",
    );
    for m in &server_stream {
        let path = method_path(iface, m);
        let camel = pascal_to_camel(&m.name);
        let getter = format!("get{}Method", m.name);
        let _ = writeln!(
            s,
            "            \"{path}\" -> {{\n                \
                 val request = {grpc_x}.{getter}().parseRequest(body.inputStream())\n                \
                 invokeServerStream {{ obs -> impl.{camel}(request, obs) }}\n                    \
                     .map {{ {grpc_x}.{getter}().streamResponse(it).readBytes() }}\n            \
             }}",
        );
    }
    s.push_str(
        "            else -> throw DriverException.Unimplemented(\"no server-streaming method at path '$path'\")\n        \
             }\n        \
             val handle = nextHandle.getAndIncrement().toULong()\n        \
             streams[handle] = messages.iterator()\n        \
             return handle\n    \
             }\n\n    \
             override suspend fun forwardStreamNext(handle: ULong): ByteArray? =\n        \
                 streams[handle]?.let { if (it.hasNext()) it.next() else null }\n\n    \
             override suspend fun forwardStreamClose(handle: ULong) {\n        \
                 streams.remove(handle)\n    \
             }\n\n",
    );

    // Declined surfaces: JSON driverCall + JSON streaming + byte plane.
    s.push_str(
        "    // The proto-first backend serves every call through the native `forward*` seams above;\n    \
             // the JSON `driverCall`/`@export`-streaming surfaces and the byte plane are unused.\n    \
             override suspend fun driverCall(uuid: String, methodName: String, argsJson: String): String =\n        \
                 throw DriverException.Unimplemented(\"native-only backend: no JSON driverCall\")\n\n    \
             override suspend fun streamingOpen(uuid: String, methodName: String, argsJson: String): ULong =\n        \
                 throw DriverException.Unimplemented(\"native-only backend: no JSON streaming\")\n\n    \
             override suspend fun streamingNext(handle: ULong): String? =\n        \
                 throw DriverException.Unimplemented(\"native-only backend: no JSON streaming\")\n\n    \
             override suspend fun streamingClose(handle: ULong) =\n        \
                 throw DriverException.Unimplemented(\"native-only backend: no JSON streaming\")\n\n    \
             override suspend fun openStream(requestJson: String): OpenStream =\n        \
                 throw DriverException.Unimplemented(\"native-only backend: no byte streams\")\n\n    \
             override suspend fun streamRead(handle: ULong): ByteArray =\n        \
                 throw DriverException.Unimplemented(\"native-only backend: no byte streams\")\n\n    \
             override suspend fun streamWrite(handle: ULong, data: ByteArray): Unit =\n        \
                 throw DriverException.Unimplemented(\"native-only backend: no byte streams\")\n\n    \
             override suspend fun streamCloseWrite(handle: ULong): Unit =\n        \
                 throw DriverException.Unimplemented(\"native-only backend: no byte streams\")\n\n    \
             override suspend fun streamClose(handle: ULong): Unit =\n        \
                 throw DriverException.Unimplemented(\"native-only backend: no byte streams\")\n",
    );

    // companion: a stable per-instance uuid, plus the StreamObserver helpers.
    s.push_str(
        "\n    private val driverUuid: String = java.util.UUID.randomUUID().toString()\n",
    );

    s.push_str(
        "\n    /**\n     \
             * Drive a unary `ImplBase` method synchronously, capturing its single response. A simple\n     \
             * service calls `onNext`+`onCompleted` inline; an `onError` is surfaced as the mapped\n     \
             * [DriverException].\n     \
             */\n    \
             private fun <T> invokeUnary(call: (StreamObserver<T>) -> Unit): T {\n        \
                 val captured = CapturingObserver<T>()\n        \
                 call(captured)\n        \
                 captured.error?.let { throw mapError(it) }\n        \
                 return captured.values.singleOrNull()\n            \
                     ?: throw DriverException.Unknown(\"unary method produced ${captured.values.size} responses\")\n    \
             }\n\n    \
             /** Drive a server-streaming `ImplBase` method synchronously, collecting every response. */\n    \
             private fun <T> invokeServerStream(call: (StreamObserver<T>) -> Unit): List<T> {\n        \
                 val captured = CapturingObserver<T>()\n        \
                 call(captured)\n        \
                 captured.error?.let { throw mapError(it) }\n        \
                 return captured.values\n    \
             }\n\n    \
             private fun mapError(t: Throwable): DriverException = when (t) {\n        \
                 is DriverException -> t\n        \
                 else -> DriverException.Unknown(t.message ?: t.toString())\n    \
             }\n\n    \
             /** A [StreamObserver] that records responses inline (the service completes synchronously). */\n    \
             private class CapturingObserver<T> : StreamObserver<T> {\n        \
                 val values = mutableListOf<T>()\n        \
                 var error: Throwable? = null\n        \
                 override fun onNext(value: T) { values.add(value) }\n        \
                 override fun onError(t: Throwable) { error = t }\n        \
                 override fun onCompleted() {}\n    \
             }\n\
         }\n",
    );

    s
}

/// The lowercase generated subpackage for the interface (`power`), used in the Kotlin `package`
/// declaration so each interface's generated glue is namespaced.
fn generated_subpackage(iface: &InterfaceRef) -> String {
    outer_class_name(iface).to_ascii_lowercase()
}

// --------------------------------------------------------------------------------------------
// Client generation

fn render_client(iface: &InterfaceRef) -> String {
    let service = &iface.service_name;
    let client = client_class_name(iface);
    let pkg = java_package(iface);
    let outer = outer_class_name(iface);
    let grpc = grpc_class(iface);
    let grpc_x = grpc_simple(iface);
    let blocking_stub = format!("{service}BlockingStub");

    let mut s = String::new();
    let _ = writeln!(
        s,
        "// @generated by jumpstarter-codegen (JavaGenerator). DO NOT EDIT.\n\
         //\n\
         // Typed client for the `{service}` interface over the existing `UniffiChannel` + the stock\n\
         // grpc-java blocking stub. `UniffiChannel` carries each stub call across UniFFI into the\n\
         // Rust core (no JVM socket); this file is just the per-method typed wrapper.\n\
         package dev.jumpstarter.generated.{snake_pkg}\n",
        snake_pkg = generated_subpackage(iface),
    );

    let _ = writeln!(
        s,
        "import com.google.protobuf.Empty\n\
         import dev.jumpstarter.client.ExporterSession\n\
         import dev.jumpstarter.client.UuidMetadataInterceptor\n\
         import {pkg}.{outer}\n\
         import {grpc}\n"
    );

    let _ = writeln!(
        s,
        "/**\n \
         * A thin typed client over the stock grpc-java [{grpc}] blocking stub, bound to one driver\n \
         * instance via [UuidMetadataInterceptor] and routed through the [ExporterSession]'s UniFFI\n \
         * channel.\n \
         *\n \
         * `open` so a CUSTOM client can subclass it to add wrapper methods or a CLI (the JVM analog\n \
         * of subclassing Python's `DriverClient`); the `stub` is `protected` for raw calls.\n \
         */\n\
         open class {client}(session: ExporterSession, driverName: String) {{\n    \
             protected val stub: {grpc}.{blocking_stub} =\n        \
                 {grpc_x}.newBlockingStub(session.channel)\n            \
                     .withInterceptors(UuidMetadataInterceptor(session.requireDriver(driverName)))\n"
    );

    for m in &iface.methods {
        if m.client_streaming {
            // Client-/bidi-streaming methods are out of scope for the proto-first milestone.
            continue;
        }
        let camel = pascal_to_camel(&m.name);
        let in_is_empty = is_empty(&m.input_type);
        let in_ty = java_message_type(iface, &m.input_type);
        let out_ty = java_message_type(iface, &m.output_type);
        let out_is_empty = is_empty(&m.output_type);

        let request_expr = if in_is_empty {
            "Empty.getDefaultInstance()".to_string()
        } else {
            // A non-Empty request is passed through verbatim (the author builds the proto).
            "request".to_string()
        };
        let param = if in_is_empty {
            String::new()
        } else {
            format!("request: {in_ty}")
        };

        if m.server_streaming {
            let _ = writeln!(
                s,
                "\n    /** `{name}` (server-streaming): returns every response message. */\n    \
                 open fun {camel}({param}): List<{out_ty}> =\n        \
                     stub.{camel}({request_expr}).asSequence().toList()",
                name = m.name,
            );
        } else if out_is_empty {
            let _ = writeln!(
                s,
                "\n    /** `{name}` (unary). */\n    \
                 open fun {camel}({param}) {{\n        \
                     stub.{camel}({request_expr})\n    \
                 }}",
                name = m.name,
            );
        } else {
            let _ = writeln!(
                s,
                "\n    /** `{name}` (unary). */\n    \
                 open fun {camel}({param}): {out_ty} =\n        \
                     stub.{camel}({request_expr})",
                name = m.name,
            );
        }
    }

    s.push_str("}\n");
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
    fn naming_helpers() {
        assert_eq!(pascal_to_camel("On"), "on");
        assert_eq!(pascal_to_camel("Off"), "off");
        assert_eq!(pascal_to_camel("Read"), "read");
        assert_eq!(pascal_to_camel("SetVoltage"), "setVoltage");
        assert_eq!(snake_to_pascal("power"), "Power");
        assert_eq!(snake_to_pascal("storage_mux"), "StorageMux");
        assert_eq!(snake_to_pascal("virtual_power"), "VirtualPower");
    }

    #[test]
    fn outer_class_from_package() {
        let iface = power_iface();
        assert_eq!(outer_class_name(&iface), "Power");
        assert_eq!(java_package(&iface), "jumpstarter.interfaces.power.v1");
        assert_eq!(grpc_class(&iface), "jumpstarter.interfaces.power.v1.PowerInterfaceGrpc");
    }

    #[test]
    fn driver_stub_skeleton_generated() {
        // The driver-side artifact is a stub impl of the stock grpc-java ImplBase (TODO bodies),
        // not a runtime adapter — the generic GrpcServiceDriverHost serves it once filled in.
        let iface = power_iface();
        let files = JavaGenerator.generate_driver(&iface);
        assert!(files.contains_key("PowerDriver.kt"), "keys: {:?}", files.keys());
        let src = &files["PowerDriver.kt"];
        assert!(src.contains("class PowerDriver : PowerInterfaceImplBase()"));
        assert!(src.contains("import jumpstarter.interfaces.power.v1.PowerInterfaceGrpc.PowerInterfaceImplBase"));
        assert!(src.contains("override fun on(request: com.google.protobuf.Empty, responseObserver: StreamObserver<com.google.protobuf.Empty>)"));
        assert!(src.contains("override fun read(request: com.google.protobuf.Empty, responseObserver: StreamObserver<Power.PowerReading>)"));
        assert!(src.contains("TODO(\"implement PowerInterface.on\")"));
    }

    /// Dev-only emitter: write the generated Power **client** into the `java/` tree so it can be
    /// wired into the build. Run with `cargo test -p jumpstarter-codegen emit_power_java -- --ignored`.
    #[test]
    #[ignore]
    fn emit_power_java() {
        let iface = power_iface();
        let base = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../java/jumpstarter-client/src/main/kotlin/dev/jumpstarter/generated/power");
        std::fs::create_dir_all(&base).unwrap();
        // Client only — proto-first drivers generate no server-side code.
        for (name, body) in JavaGenerator.generate_client(&iface) {
            std::fs::write(base.join(name), body).unwrap();
        }
    }

    #[test]
    fn client_output_keyed_by_client_file() {
        let iface = power_iface();
        let files = JavaGenerator.generate_client(&iface);
        assert!(files.contains_key("PowerClient.kt"), "keys: {:?}", files.keys());
        let src = &files["PowerClient.kt"];
        // `open class` + `open fun` so a custom client can subclass and add wrapper methods/CLI.
        assert!(src.contains("open class PowerClient(session: ExporterSession, driverName: String)"));
        assert!(src.contains("PowerInterfaceGrpc.newBlockingStub(session.channel)"));
        // Unary on()/off(); read() server-streaming returns a list of PowerReading.
        assert!(src.contains("open fun on()"));
        assert!(src.contains("open fun off()"));
        assert!(src.contains("fun read(): List<Power.PowerReading>"));
        assert!(src.contains("Empty.getDefaultInstance()"));
    }
}
