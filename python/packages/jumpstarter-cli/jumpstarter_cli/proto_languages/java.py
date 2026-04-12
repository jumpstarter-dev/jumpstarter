"""Java driver client package generator for `jmp proto generate --language java`.

Given a FileDescriptorProto, produces a complete, standalone Java driver client
package with:
  - build.gradle.kts (protobuf compilation + gRPC plugin)
  - src/main/proto/{name}.proto (with java_package option)
  - src/main/java/{package_path}/{Service}Client.java (typed client)
"""

from __future__ import annotations

import re

from google.protobuf.descriptor_pb2 import (
    FieldDescriptorProto,
    FileDescriptorProto,
    MethodDescriptorProto,
)

from . import register_language

# ---------------------------------------------------------------------------
# Name conversion helpers
# ---------------------------------------------------------------------------

_SCALAR_TYPE_NAMES = {
    FieldDescriptorProto.TYPE_DOUBLE: "double",
    FieldDescriptorProto.TYPE_FLOAT: "float",
    FieldDescriptorProto.TYPE_INT64: "int64",
    FieldDescriptorProto.TYPE_UINT64: "uint64",
    FieldDescriptorProto.TYPE_INT32: "int32",
    FieldDescriptorProto.TYPE_FIXED64: "fixed64",
    FieldDescriptorProto.TYPE_FIXED32: "fixed32",
    FieldDescriptorProto.TYPE_BOOL: "bool",
    FieldDescriptorProto.TYPE_STRING: "string",
    FieldDescriptorProto.TYPE_BYTES: "bytes",
    FieldDescriptorProto.TYPE_UINT32: "uint32",
    FieldDescriptorProto.TYPE_SFIXED32: "sfixed32",
    FieldDescriptorProto.TYPE_SFIXED64: "sfixed64",
    FieldDescriptorProto.TYPE_SINT32: "sint32",
    FieldDescriptorProto.TYPE_SINT64: "sint64",
}

_JAVA_SCALAR_MAP = {
    "double": "double",
    "float": "float",
    "int32": "int",
    "int64": "long",
    "uint32": "int",
    "uint64": "long",
    "sint32": "int",
    "sint64": "long",
    "fixed32": "int",
    "fixed64": "long",
    "sfixed32": "int",
    "sfixed64": "long",
    "bool": "boolean",
    "string": "String",
    "bytes": "com.google.protobuf.ByteString",
}

_JAVA_BOXED = {
    "double": "Double",
    "float": "Float",
    "int": "Integer",
    "long": "Long",
    "boolean": "Boolean",
}


def _pascal_to_camel(name: str) -> str:
    """Convert PascalCase to camelCase: 'ReadPower' -> 'readPower'."""
    if not name:
        return name
    # Handle consecutive capitals: "HTTPSConnect" -> "httpsConnect"
    i = 0
    while i < len(name) - 1 and name[i].isupper() and name[i + 1].isupper():
        i += 1
    if i == 0:
        return name[0].lower() + name[1:]
    # All uppercase or leading acronym
    return name[:i].lower() + name[i:]


def _proto_filename_to_outer_class(proto_name: str) -> str:
    """Derive the protobuf outer class name from proto filename.

    power.proto -> Power
    network_interface.proto -> NetworkInterface
    """
    base = proto_name.rsplit("/", 1)[-1]
    if base.endswith(".proto"):
        base = base[:-6]
    # Convert snake_case to PascalCase
    return "".join(p.capitalize() for p in base.split("_"))


def _resolve_type_name(type_name: str) -> str:
    """Strip leading dot from fully-qualified type names."""
    if type_name.startswith("."):
        return type_name[1:]
    return type_name


def _short_type(type_name: str) -> str:
    """Get the short (unqualified) name from a fully-qualified type."""
    return type_name.rsplit(".", 1)[-1]


def _is_empty_type(type_name: str) -> bool:
    """Check if a type is google.protobuf.Empty."""
    resolved = _resolve_type_name(type_name)
    return resolved == "google.protobuf.Empty" or resolved.endswith(".Empty") and "google.protobuf" in resolved


def _java_type_for_scalar(scalar_name: str) -> str:
    return _JAVA_SCALAR_MAP.get(scalar_name, "Object")


def _boxed(java_type: str) -> str:
    return _JAVA_BOXED.get(java_type, java_type)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


def generate_java_driver_package(
    fd: FileDescriptorProto,
    output_dir: str,
    output_package: str,
    proto_source: str,
) -> dict[str, str]:
    """Generate a complete Java driver client package from a FileDescriptorProto."""
    files: dict[str, str] = {}

    proto_name = fd.name or "driver.proto"
    service = fd.service[0]
    service_name = service.name
    outer_class = _proto_filename_to_outer_class(proto_name)

    # 1. Proto source with java_package option
    files[f"src/main/proto/{proto_name}"] = _patch_proto_source(
        proto_source, output_package
    )

    # 2. Client class
    client_name = service_name.replace("Interface", "") + "Client"
    package_path = output_package.replace(".", "/")
    files[f"src/main/java/{package_path}/{client_name}.java"] = _generate_client(
        fd, service, client_name, output_package, outer_class
    )

    # 3. build.gradle.kts
    files["build.gradle.kts"] = _generate_build_gradle(output_package)

    return files


# ---------------------------------------------------------------------------
# Proto source patching
# ---------------------------------------------------------------------------


def _patch_proto_source(proto_source: str, java_package: str) -> str:
    """Add java_package option to proto source if not already present."""
    if "option java_package" in proto_source:
        return proto_source

    # Insert after the package declaration
    lines = proto_source.split("\n")
    result = []
    inserted = False
    for line in lines:
        result.append(line)
        if not inserted and line.strip().startswith("package ") and line.strip().endswith(";"):
            result.append(f'option java_package = "{java_package}";')
            inserted = True

    if not inserted:
        # Fallback: prepend
        result.insert(0, f'option java_package = "{java_package}";')

    return "\n".join(result)


# ---------------------------------------------------------------------------
# Client class generation
# ---------------------------------------------------------------------------


def _generate_client(
    fd: FileDescriptorProto,
    service: MethodDescriptorProto,
    client_name: str,
    package: str,
    outer_class: str,
) -> str:
    lines: list[str] = []
    service_name = service.name
    grpc_class = f"{service_name}Grpc"

    # Detect what imports we need
    has_server_streaming = False
    has_portforward = False
    has_empty = False

    for method in service.method:
        if method.client_streaming:
            has_portforward = True
        elif method.server_streaming:
            has_server_streaming = True
        if _is_empty_type(method.input_type) or _is_empty_type(method.output_type):
            has_empty = True

    # Package
    lines.append(f"package {package};")
    lines.append("")

    # Imports
    lines.append("import dev.jumpstarter.client.ExporterSession;")
    lines.append("import dev.jumpstarter.client.UuidMetadataInterceptor;")
    if has_portforward:
        lines.append("import dev.jumpstarter.client.TcpPortforwardAdapter;")
    lines.append("import io.grpc.Channel;")
    if has_empty:
        lines.append("import com.google.protobuf.Empty;")
    if has_server_streaming:
        lines.append("import java.util.Iterator;")
    if has_portforward:
        lines.append("import java.net.InetSocketAddress;")
        lines.append("import org.jetbrains.annotations.Nullable;")
    lines.append("")

    # Class
    if has_portforward:
        lines.append(f"public class {client_name} implements AutoCloseable {{")
    else:
        lines.append(f"public class {client_name} {{")
    lines.append("")

    # Fields
    if has_portforward:
        lines.append("    private final ExporterSession session;")
        lines.append("    private final String driverUuid;")
    lines.append(f"    private final {grpc_class}.{service_name}BlockingStub stub;")
    if has_portforward:
        # One adapter field per bidi method
        for method in service.method:
            if method.client_streaming:
                field_name = f"{_pascal_to_camel(method.name)}Adapter"
                lines.append(f"    private @Nullable TcpPortforwardAdapter {field_name};")
    lines.append("")

    # Constructor
    lines.append(f"    public {client_name}(ExporterSession session, String driverName) {{")
    lines.append('        String uuid = session.getReport().findByName(driverName).getUuid();')
    lines.append("        Channel channel = session.getChannel();")
    if has_portforward:
        lines.append("        this.session = session;")
        lines.append("        this.driverUuid = uuid;")
    lines.append(f"        this.stub = {grpc_class}.newBlockingStub(channel)")
    lines.append("            .withInterceptors(new UuidMetadataInterceptor(uuid));")
    lines.append("    }")
    lines.append("")

    # Methods
    for method in service.method:
        if method.client_streaming:
            # @exportstream — generate portforward method
            _generate_portforward_method(lines, method)
        elif method.server_streaming:
            _generate_server_streaming_method(lines, method, outer_class)
        else:
            _generate_unary_method(lines, method, outer_class, fd)

    # AutoCloseable.close() if we have portforward adapters
    if has_portforward:
        lines.append("    @Override")
        lines.append("    public void close() {")
        for method in service.method:
            if method.client_streaming:
                field_name = f"{_pascal_to_camel(method.name)}Adapter"
                lines.append(f"        if ({field_name} != null) {{")
                lines.append(f"            {field_name}.close();")
                lines.append("        }")
        lines.append("    }")
        lines.append("")

    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _generate_unary_method(
    lines: list[str],
    method: MethodDescriptorProto,
    outer_class: str,
    fd: FileDescriptorProto,
) -> None:
    java_name = _pascal_to_camel(method.name)
    input_type = _resolve_type_name(method.input_type)
    output_type = _resolve_type_name(method.output_type)
    input_is_empty = _is_empty_type(method.input_type)
    output_is_empty = _is_empty_type(method.output_type)

    input_short = _short_type(input_type)
    output_short = _short_type(output_type)

    # Build parameter list and call expression
    if input_is_empty:
        params_str = ""
        call_expr = "Empty.getDefaultInstance()"
    else:
        params, call_expr = _build_method_params(input_short, fd, outer_class)
        params_str = ", ".join(params)

    # Return type
    if output_is_empty:
        ret_type = "void"
    else:
        ret_type = f"{outer_class}.{output_short}"

    if ret_type == "void":
        lines.append(f"    public void {java_name}({params_str}) {{")
        lines.append(f"        stub.{java_name}({call_expr});")
    else:
        lines.append(f"    public {ret_type} {java_name}({params_str}) {{")
        lines.append(f"        return stub.{java_name}({call_expr});")
    lines.append("    }")
    lines.append("")


def _generate_server_streaming_method(
    lines: list[str],
    method: MethodDescriptorProto,
    outer_class: str,
) -> None:
    java_name = _pascal_to_camel(method.name)
    output_type = _resolve_type_name(method.output_type)
    output_short = _short_type(output_type)
    input_is_empty = _is_empty_type(method.input_type)

    if input_is_empty:
        params_str = ""
        call_expr = "Empty.getDefaultInstance()"
    else:
        input_short = _short_type(_resolve_type_name(method.input_type))
        params_str = f"{outer_class}.{input_short} request"
        call_expr = "request"

    if _is_empty_type(method.output_type):
        ret_type = "Iterator<Empty>"
    else:
        ret_type = f"Iterator<{outer_class}.{output_short}>"

    lines.append(f"    public {ret_type} {java_name}({params_str}) {{")
    lines.append(f"        return stub.{java_name}({call_expr});")
    lines.append("    }")
    lines.append("")


def _generate_portforward_method(
    lines: list[str],
    method: MethodDescriptorProto,
) -> None:
    java_name = _pascal_to_camel(method.name)
    field_name = f"{java_name}Adapter"
    # The stream method name is the lowercase proto method name
    stream_method = _pascal_to_camel(method.name)

    lines.append(f"    public InetSocketAddress {java_name}Tcp() {{")
    lines.append(f"        if ({field_name} != null) {{")
    lines.append(f"            {field_name}.close();")
    lines.append("        }")
    lines.append(f'        {field_name} = TcpPortforwardAdapter.open(session, driverUuid, "{stream_method}");')
    lines.append(f"        return {field_name}.getLocalAddress();")
    lines.append("    }")
    lines.append("")


def _build_method_params(
    input_short: str,
    fd: FileDescriptorProto,
    outer_class: str,
) -> tuple[list[str], str]:
    """Build Java method parameters from the input message fields.

    Returns (parameter_list, call_builder_expression).
    """
    # Find the input message in the FileDescriptorProto
    input_msg = None
    for msg in fd.message_type:
        if msg.name == input_short:
            input_msg = msg
            break

    if input_msg is None or not input_msg.field:
        return [], f"{outer_class}.{input_short}.getDefaultInstance()"

    params: list[str] = []
    builder_calls: list[str] = []

    for field in input_msg.field:
        # Determine Java type
        if field.type in (FieldDescriptorProto.TYPE_MESSAGE, FieldDescriptorProto.TYPE_ENUM):
            type_name = _resolve_type_name(field.type_name)
            short_name = _short_type(type_name)
            # Check if it's a well-known type
            if "google.protobuf" in type_name:
                java_type = short_name
            else:
                java_type = f"{outer_class}.{short_name}"
        else:
            scalar_name = _SCALAR_TYPE_NAMES.get(field.type, "unknown")
            java_type = _java_type_for_scalar(scalar_name)

        # Convert field name to camelCase
        param_name = _snake_to_camel(field.name)
        setter_name = _snake_to_pascal(field.name)

        if field.label == FieldDescriptorProto.LABEL_REPEATED:
            java_type = f"java.util.List<{_boxed(java_type)}>"
            setter = f"addAll{setter_name}"
        else:
            setter = f"set{setter_name}"

        params.append(f"{java_type} {param_name}")
        builder_calls.append(f".{setter}({param_name})")

    call_expr = (
        f"{outer_class}.{input_short}.newBuilder()"
        + "".join(builder_calls)
        + ".build()"
    )
    return params, call_expr


def _snake_to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _snake_to_pascal(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_"))


# ---------------------------------------------------------------------------
# build.gradle.kts generation
# ---------------------------------------------------------------------------


def _generate_build_gradle(output_package: str) -> str:
    lines: list[str] = []
    lines.append("plugins {")
    lines.append("    java")
    lines.append('    id("com.google.protobuf") version "0.9.4"')
    lines.append("}")
    lines.append("")
    lines.append("java {")
    lines.append("    sourceCompatibility = JavaVersion.VERSION_17")
    lines.append("    targetCompatibility = JavaVersion.VERSION_17")
    lines.append("}")
    lines.append("")
    lines.append("dependencies {")
    lines.append('    implementation("dev.jumpstarter:jumpstarter-client:0.1.0-SNAPSHOT")')
    lines.append('    implementation("io.grpc:grpc-netty-shaded:1.68.1")')
    lines.append('    implementation("io.grpc:grpc-protobuf:1.68.1")')
    lines.append('    implementation("io.grpc:grpc-stub:1.68.1")')
    lines.append('    implementation("com.google.protobuf:protobuf-java:4.28.3")')
    lines.append('    implementation("org.jetbrains:annotations:26.0.1")')
    lines.append('    compileOnly("org.apache.tomcat:annotations-api:6.0.53")')
    lines.append("}")
    lines.append("")
    lines.append("protobuf {")
    lines.append("    protoc {")
    lines.append('        artifact = "com.google.protobuf:protoc:4.28.3"')
    lines.append("    }")
    lines.append("    plugins {")
    lines.append('        create("grpc") {')
    lines.append('            artifact = "io.grpc:protoc-gen-grpc-java:1.68.1"')
    lines.append("        }")
    lines.append("    }")
    lines.append("    generateProtoTasks {")
    lines.append("        all().forEach { task ->")
    lines.append("            task.plugins {")
    lines.append('                create("grpc")')
    lines.append("            }")
    lines.append("        }")
    lines.append("    }")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


register_language("java", generate_java_driver_package)
