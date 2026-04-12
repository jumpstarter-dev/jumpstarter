"""Rust language generator for jmp codegen.

Generates:
  1. Per-interface typed client structs wrapping tonic gRPC stubs
  2. ExporterClass device wrapper composing interface clients
  3. `#[jumpstarter_test]` proc macro usage in test fixtures
  4. Cargo.toml package metadata with tonic-build for proto compilation
"""

from __future__ import annotations

import re

from ..engine import LanguageGenerator, register_language
from ..models import (
    CodegenContext,
    DriverInterfaceRef,
    InterfaceMethod,
    Optionality,
    ProtoMessage,
)


def _snake_to_pascal(name: str) -> str:
    """Convert snake-case or kebab-case to PascalCase."""
    return "".join(part.capitalize() for part in re.split(r"[-_]", name))


def _pascal_to_snake(name: str) -> str:
    """Convert PascalCase to snake_case."""
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    s = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s)
    return s.lower()


def _proto_package_to_module(proto_package: str) -> str:
    """Convert proto package to Rust module path.

    e.g., jumpstarter.interfaces.power.v1 → power::v1
    """
    parts = proto_package.split(".")
    if len(parts) >= 3 and parts[0] == "jumpstarter" and parts[1] == "interfaces":
        return "::".join(parts[2:])
    return "::".join(parts)


def _rust_type_for_proto(type_name: str) -> str:
    """Map proto scalar types to Rust types."""
    mapping = {
        "double": "f64",
        "float": "f32",
        "int32": "i32",
        "int64": "i64",
        "uint32": "u32",
        "uint64": "u64",
        "sint32": "i32",
        "sint64": "i64",
        "fixed32": "u32",
        "fixed64": "u64",
        "sfixed32": "i32",
        "sfixed64": "i64",
        "bool": "bool",
        "string": "String",
        "bytes": "Vec<u8>",
    }
    return mapping.get(type_name, type_name)


def _format_doc_comment(comment: str | None, indent: str = "") -> str:
    """Format a proto comment as Rust /// doc comments."""
    if not comment:
        return ""
    lines = comment.strip().split("\n")
    return "\n".join(f"{indent}/// {line.strip()}" for line in lines) + "\n"


def _method_signature(method: InterfaceMethod, proto_mod: str) -> str:
    """Generate a Rust method signature for an interface method."""
    name = _pascal_to_snake(method.name)

    # Determine return type
    if method.stream_constructor:
        return_type = (
            "Result<(tokio::sync::mpsc::Sender<bytes::Bytes>, "
            "tokio::sync::mpsc::Receiver<bytes::Bytes>), tonic::Status>"
        )
    elif method.server_streaming:
        out_type = _resolve_rust_type(method.output_type, proto_mod)
        return_type = f"Result<tonic::Streaming<{out_type}>, tonic::Status>"
    else:
        out_type = _resolve_rust_type(method.output_type, proto_mod)
        return_type = f"Result<{out_type}, tonic::Status>"

    # Determine input
    if _is_empty_type(method.input_type):
        params = "&mut self"
    else:
        in_type = _resolve_rust_type(method.input_type, proto_mod)
        params = f"&mut self, request: {in_type}"

    return f"    pub async fn {name}({params}) -> {return_type}"


def _resolve_rust_type(proto_type: str, proto_mod: str) -> str:
    """Resolve a proto type to a Rust type."""
    if _is_empty_type(proto_type):
        return "()"
    # For types in the same proto package, use the module-qualified name
    parts = proto_type.rsplit(".", 1)
    if len(parts) == 2:
        return f"{proto_mod}::{parts[1]}"
    return proto_type


def _is_empty_type(type_name: str) -> bool:
    """Check if a proto type is google.protobuf.Empty."""
    return type_name in ("google.protobuf.Empty", ".google.protobuf.Empty")


def _resolve_rust_import(interface: DriverInterfaceRef) -> tuple[str, str, bool]:
    """Resolve the Rust import for an interface client.

    Returns (use_path, type_name, is_external).
    When is_external is True, the client comes from an external crate
    and should not be generated.
    """
    hint = interface.drivers.get("rust")
    if hint and hint.client_class:
        # hint.client_class is like "jumpstarter_driver_network::NetworkClient"
        if "::" in hint.client_class:
            parts = hint.client_class.rsplit("::", 1)
            return parts[0], parts[1], True
        elif hint.package:
            crate = hint.package.replace("-", "_")
            return crate, hint.client_class, True

    # Convention: use generated local client
    client_name = interface.service_name.replace("Interface", "Client")
    mod_name = _pascal_to_snake(interface.service_name).replace("_interface", "")
    return f"crate::clients::{mod_name}_client", client_name, False


def _gen_interface_client(
    interface: DriverInterfaceRef,
    crate_name: str,
) -> str:
    """Generate a typed client struct for a single interface."""
    service_name = interface.service_name
    client_name = service_name.replace("Interface", "Client")
    snake_module = _pascal_to_snake(service_name)
    proto_mod = _proto_package_to_module(interface.proto_package)

    lines: list[str] = [
        "// Auto-generated by `jmp codegen --language rust`. Do not edit.",
        "",
        "use tonic::transport::Channel;",
        "use tower::ServiceBuilder;",
        "use jumpstarter_client::UuidInterceptor;",
        "use jumpstarter_client::ExporterSession;",
        "",
        f"pub mod {snake_module}_proto {{",
        f'    tonic::include_proto!("{interface.proto_package}");',
        "}",
        "",
        f"use {snake_module}_proto::{_pascal_to_snake(service_name)}_client::{service_name}Client as GrpcClient;",
        "",
    ]

    # Doc comment for the client struct
    doc = _format_doc_comment(interface.doc_comment)
    if doc:
        lines.append(doc.rstrip())

    lines.extend([
        f"pub struct {client_name} {{",
        "    stub: GrpcClient<tonic::service::interceptor::InterceptedService<Channel, UuidInterceptor>>,",
        "}",
        "",
        f"impl {client_name} {{",
        "    /// Create a new client for the named driver instance.",
        "    pub fn new(session: &ExporterSession, driver_name: &str) -> Self {",
        '        let uuid = session.report().find_by_name(driver_name)',
        '            .unwrap_or_else(|| panic!("driver \'{}\' not found in device tree", driver_name))',
        "            .uuid()",
        "            .to_owned();",
        "        let channel = session.channel().clone();",
        "        let stub = GrpcClient::with_interceptor(channel, UuidInterceptor::new(uuid));",
        "        Self { stub }",
        "    }",
    ])

    # Generate methods
    for method in interface.methods:
        lines.append("")
        doc = _format_doc_comment(method.doc_comment, "    ")
        if doc:
            lines.append(doc.rstrip())

        sig = _method_signature(method, f"{snake_module}_proto")
        lines.append(f"{sig} {{")

        name = _pascal_to_snake(method.name)
        if method.stream_constructor:
            lines.extend([
                "        // @exportstream — use StreamChannel for bidi byte transfer",
                "        jumpstarter_client::StreamChannel::open(session.channel().clone())",
                "            .await",
                '            .map_err(|e| tonic::Status::internal(e.to_string()))',
            ])
        elif method.server_streaming:
            if _is_empty_type(method.input_type):
                lines.append(f"        let response = self.stub.{name}(()).await?;")
            else:
                lines.append(f"        let response = self.stub.{name}(request).await?;")
            lines.append("        Ok(response.into_inner())")
        else:
            if _is_empty_type(method.input_type):
                lines.append(f"        let response = self.stub.{name}(()).await?;")
            else:
                lines.append(f"        let response = self.stub.{name}(request).await?;")
            lines.append("        Ok(response.into_inner())")
        lines.append("    }")

    lines.extend([
        "}",
        "",
    ])

    return "\n".join(lines)


def _gen_device_wrapper(ctx: CodegenContext) -> str:
    """Generate the ExporterClass device wrapper struct."""
    ec = ctx.exporter_class
    struct_name = _snake_to_pascal(ec.name) + "Device"

    lines: list[str] = [
        "// Auto-generated by `jmp codegen --language rust`. Do not edit.",
        "",
        "use jumpstarter_client::ExporterSession;",
        "",
    ]

    # Import all interface client types (external crates or local modules)
    for iface in ec.interfaces:
        use_path, client_name, is_external = _resolve_rust_import(iface)
        if is_external:
            lines.append(f"use {use_path}::{client_name};")
        else:
            mod_name = _pascal_to_snake(iface.service_name).replace("_interface", "")
            lines.append(f"mod {mod_name}_client;")
            lines.append(f"use {mod_name}_client::{client_name};")
    lines.append("")

    # Struct definition
    lines.extend([
        f"/// Typed device wrapper for ExporterClass `{ec.name}`.",
        "///",
        "/// Composes per-interface clients into a single device object.",
        "/// Required interfaces are guaranteed; optional ones are `Option<T>`.",
        f"pub struct {struct_name}<'a> {{",
    ])

    for iface in ec.interfaces:
        client_name = iface.service_name.replace("Interface", "Client")
        doc = _format_doc_comment(iface.doc_comment, "    ")
        if doc:
            lines.append(doc.rstrip())
        if iface.optionality == Optionality.OPTIONAL:
            lines.append(f"    pub {iface.name}: Option<{client_name}>,")
        else:
            lines.append(f"    pub {iface.name}: {client_name},")

    lines.extend([
        "    _session: &'a ExporterSession,",
        "}",
        "",
    ])

    # Constructor
    lines.extend([
        f"impl<'a> {struct_name}<'a> {{",
        "    /// Create a new device wrapper from an exporter session.",
        "    pub fn new(session: &'a ExporterSession) -> Self {",
    ])

    for iface in ec.interfaces:
        client_name = iface.service_name.replace("Interface", "Client")
        if iface.optionality == Optionality.OPTIONAL:
            lines.extend([
                f'        let {iface.name} = if session.report().find_by_name("{iface.name}").is_some() {{',
                f'            Some({client_name}::new(session, "{iface.name}"))',
                "        } else {",
                "            None",
                "        };",
            ])
        else:
            lines.append(
                f'        let {iface.name} = {client_name}::new(session, "{iface.name}");'
            )

    lines.append(f"        Self {{")
    for iface in ec.interfaces:
        lines.append(f"            {iface.name},")
    lines.append("            _session: session,")
    lines.extend([
        "        }",
        "    }",
        "}",
        "",
    ])

    return "\n".join(lines)


def _gen_test_fixture(ctx: CodegenContext) -> str:
    """Generate a test example using #[jumpstarter_test]."""
    ec = ctx.exporter_class
    struct_name = _snake_to_pascal(ec.name) + "Device"
    mod_name = ec.name.replace("-", "_")

    lines: list[str] = [
        "// Auto-generated test example by `jmp codegen --language rust --test-fixtures`.",
        "// Do not edit — regenerate when the ExporterClass changes.",
        "",
        "use jumpstarter_testing::jumpstarter_test;",
        "",
        f"use crate::devices::{mod_name}::{struct_name};",
        "",
        "/// Example test showing how to use the generated device wrapper.",
        "///",
        f"/// The `#[jumpstarter_test]` macro automatically creates an `ExporterSession`",
        "/// from the `JUMPSTARTER_HOST` environment variable and constructs the typed",
        "/// device wrapper.",
        "#[jumpstarter_test]",
        f"async fn test_{mod_name}_smoke(device: {struct_name}<'_>) {{",
        "    // Access typed driver clients directly:",
    ]

    # Add a comment for each interface
    for iface in ec.interfaces:
        if iface.optionality == Optionality.OPTIONAL:
            lines.append(f"    // if let Some(ref mut {iface.name}) = device.{iface.name} {{ /* ... */ }}")
        else:
            lines.append(f"    // device.{iface.name}.method_name(()).await.unwrap();")

    lines.extend([
        "}",
        "",
    ])

    return "\n".join(lines)


def _gen_cargo_toml(ctx: CodegenContext) -> str:
    """Generate a standalone Cargo.toml for the generated crate.

    Emits concrete version specs so the crate works outside any workspace.
    When used inside the jumpstarter monorepo, the workspace Cargo.toml
    can override versions via [workspace.dependencies].
    """
    ec = ctx.exporter_class
    crate_name = ctx.package_name or f"jumpstarter-{ec.name}"

    # Version specs for standalone use (outside any workspace)
    JUMPSTARTER_VERSION = "0.1.0"
    TONIC_VERSION = "0.13"
    PROST_VERSION = "0.13"

    lines: list[str] = [
        "[package]",
        f'name = "{crate_name}"',
        f'version = "{JUMPSTARTER_VERSION}"',
        'edition = "2021"',
        "",
        "# Auto-generated by `jmp codegen --language rust`.",
        "# To use inside a Cargo workspace, replace version strings with",
        "# `{ workspace = true }` references.",
        "",
        "[dependencies]",
        f'jumpstarter-client = "{JUMPSTARTER_VERSION}"',
        f'jumpstarter-testing = "{JUMPSTARTER_VERSION}"',
        f'tonic = "{TONIC_VERSION}"',
        f'prost = "{PROST_VERSION}"',
        'tokio = { version = "1", features = ["full"] }',
        'tower = "0.5"',
        'bytes = "1"',
    ]

    # Add external driver crate dependencies
    for iface in ec.interfaces:
        hint = iface.drivers.get("rust")
        if hint and hint.client_class and hint.package:
            crate = hint.package
            version = hint.version or JUMPSTARTER_VERSION
            lines.append(f'{crate} = "{version}"')

    lines.extend([
        "",
        "[build-dependencies]",
        f'tonic-build = "{TONIC_VERSION}"',
    ])

    return "\n".join(lines)


def _gen_build_rs(ctx: CodegenContext) -> str:
    """Generate a build.rs that compiles interface protos with tonic-build."""
    ec = ctx.exporter_class
    proto_paths = []
    for iface in ec.interfaces:
        if iface.proto_file_path:
            proto_paths.append(iface.proto_file_path)

    lines: list[str] = [
        "// Auto-generated by `jmp codegen --language rust`. Do not edit.",
        "",
        "fn main() -> Result<(), Box<dyn std::error::Error>> {",
        "    tonic_build::configure()",
        "        .build_server(false)",
        "        .compile_protos(",
        "            &[",
    ]

    for path in proto_paths:
        lines.append(f'                "{path}",')

    lines.extend([
        "            ],",
        '            &["proto"],',
        "        )?;",
        "    Ok(())",
        "}",
    ])

    return "\n".join(lines)


class RustLanguageGenerator(LanguageGenerator):
    """Rust code generator for jmp codegen.

    Generates:
      1. Per-interface typed client structs wrapping tonic gRPC stubs
      2. ExporterClass device wrapper struct with lifetime-parameterized session reference
      3. Test fixture example using `#[jumpstarter_test]` proc macro
      4. Cargo.toml and build.rs for proto compilation
    """

    @property
    def language_name(self) -> str:
        return "rust"

    def generate_interface_client(
        self, ctx: CodegenContext, interface: DriverInterfaceRef,
    ) -> dict[str, str]:
        """Generate a typed client for a single driver interface."""
        # If an external Rust crate provides the client, skip generation
        _, _, is_external = _resolve_rust_import(interface)
        if is_external:
            return {}

        crate_name = ctx.package_name or f"jumpstarter-{ctx.exporter_class.name}"
        mod_name = _pascal_to_snake(interface.service_name).replace("_interface", "")

        source = _gen_interface_client(interface, crate_name)
        return {
            f"src/clients/{mod_name}_client.rs": source,
        }

    def generate_device_wrapper(self, ctx: CodegenContext) -> dict[str, str]:
        """Generate the ExporterClass device wrapper module."""
        ec = ctx.exporter_class
        mod_name = ec.name.replace("-", "_")

        source = _gen_device_wrapper(ctx)

        # Generate a mod.rs that re-exports the device
        mod_rs_lines = [
            "// Auto-generated by `jmp codegen --language rust`. Do not edit.",
            "",
            f"pub mod {mod_name};",
        ]

        return {
            f"src/devices/{mod_name}.rs": source,
            "src/devices/mod.rs": "\n".join(mod_rs_lines) + "\n",
        }

    def generate_test_fixture(self, ctx: CodegenContext) -> dict[str, str]:
        """Generate test example using #[jumpstarter_test]."""
        ec = ctx.exporter_class
        mod_name = ec.name.replace("-", "_")

        source = _gen_test_fixture(ctx)
        return {
            f"tests/{mod_name}_test.rs": source,
        }

    def generate_package_metadata(self, ctx: CodegenContext) -> dict[str, str]:
        """Generate Cargo.toml and build.rs."""
        files: dict[str, str] = {
            "Cargo.toml": _gen_cargo_toml(ctx),
        }

        # Only generate build.rs if we have proto file paths
        if any(i.proto_file_path for i in ctx.exporter_class.interfaces):
            files["build.rs"] = _gen_build_rs(ctx)

        return files


# Register with the engine
register_language("rust", RustLanguageGenerator)
