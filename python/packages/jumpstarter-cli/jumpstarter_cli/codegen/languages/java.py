"""Java language generator for jmp codegen.

Generates:
  - Per-interface typed client classes wrapping protoc-generated gRPC blocking stubs
  - ExporterClass device wrapper composing interface clients
  - JUnit 5 test fixture (JumpstarterExtension + @JumpstarterDevice wiring)
  - Gradle build files with protobuf compilation configuration
"""

from __future__ import annotations

import re

from ..engine import LanguageGenerator, register_language
from ..models import (
    CodegenContext,
    DriverInterfaceRef,
    ExporterClassSpec,
    InterfaceMethod,
    MessageField,
    Optionality,
    ProtoMessage,
)

# ---------------------------------------------------------------------------
# Name conversion helpers
# ---------------------------------------------------------------------------

def _snake_to_camel(name: str) -> str:
    """Convert snake_case to camelCase."""
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _snake_to_pascal(name: str) -> str:
    """Convert snake_case to PascalCase."""
    return "".join(p.capitalize() for p in name.split("_"))


def _kebab_to_pascal(name: str) -> str:
    """Convert kebab-case to PascalCase."""
    return "".join(p.capitalize() for p in name.split("-"))


def _proto_package_to_java(proto_package: str) -> str:
    """Convert proto package to Java package.

    e.g. jumpstarter.interfaces.power.v1 → dev.jumpstarter.interfaces.power.v1
    """
    if proto_package.startswith("jumpstarter."):
        return "dev." + proto_package
    return proto_package


def _java_type_for_field(field: MessageField) -> str:
    """Map a proto field type to its Java type."""
    if field.is_message:
        # Use the short name — it will be in the same generated package
        return field.type_name.rsplit(".", 1)[-1]
    if field.is_enum:
        return field.type_name.rsplit(".", 1)[-1]

    scalar_map = {
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
    return scalar_map.get(field.type_name, "Object")


def _java_boxed_type(java_type: str) -> str:
    """Return the boxed version of a primitive Java type."""
    boxed = {
        "double": "Double",
        "float": "Float",
        "int": "Integer",
        "long": "Long",
        "boolean": "Boolean",
    }
    return boxed.get(java_type, java_type)


def _service_to_grpc_class(service_name: str) -> str:
    """Convert service name to gRPC generated class name.

    e.g. PowerInterface → PowerInterfaceGrpc
    """
    return service_name + "Grpc"


def _service_to_client_name(service_name: str) -> str:
    """Convert service name to client class name.

    e.g. PowerInterface → PowerClient
    """
    return service_name.replace("Interface", "Client")


def _wrap_javadoc(text: str | None, indent: str = "    ") -> str:
    """Wrap text in a Javadoc comment."""
    if not text:
        return ""
    lines = text.strip().split("\n")
    result = [f"{indent}/**"]
    for line in lines:
        line = line.strip()
        if line.startswith("//"):
            line = line[2:].strip()
        result.append(f"{indent} * {line}" if line else f"{indent} *")
    result.append(f"{indent} */")
    return "\n".join(result) + "\n"


# ---------------------------------------------------------------------------
# Client import resolution
# ---------------------------------------------------------------------------


def _resolve_java_import(interface: DriverInterfaceRef) -> tuple[str, str, bool]:
    """Resolve the Java import for an interface client.

    Returns (fully_qualified_class, short_class_name, is_external).
    When is_external is True, the client comes from an external package
    and should not be generated. When False, we generate a stub client.
    """
    hint = interface.drivers.get("java")
    if hint and hint.client_class:
        client_class = hint.client_class
        if "." in client_class:
            short = client_class.rsplit(".", 1)[-1]
            return client_class, short, True
        elif hint.package:
            fqn = f"{hint.package}.{client_class}"
            return fqn, client_class, True

    # Convention: generate from proto package
    java_package = _proto_package_to_java(interface.proto_package)
    client_name = _service_to_client_name(interface.service_name)
    return f"{java_package}.{client_name}", client_name, False


# ---------------------------------------------------------------------------
# Java generator
# ---------------------------------------------------------------------------


class JavaLanguageGenerator(LanguageGenerator):

    @property
    def language_name(self) -> str:
        return "java"

    def generate_interface_client(
        self, ctx: CodegenContext, interface: DriverInterfaceRef,
    ) -> dict[str, str]:
        # If an external Java client package is declared, skip generation
        _, _, is_external = _resolve_java_import(interface)
        if is_external:
            return {}

        java_package = _proto_package_to_java(interface.proto_package)
        package_path = java_package.replace(".", "/")
        client_name = _service_to_client_name(interface.service_name)
        grpc_class = _service_to_grpc_class(interface.service_name)

        lines: list[str] = []
        lines.append(f"package {java_package};")
        lines.append("")
        lines.append("import dev.jumpstarter.client.ExporterSession;")
        lines.append("import dev.jumpstarter.client.UuidMetadataInterceptor;")
        lines.append("import io.grpc.Channel;")
        lines.append("import com.google.protobuf.Empty;")

        # Check if we need Iterator import
        has_server_streaming = any(
            m.server_streaming and not m.client_streaming for m in interface.methods
        )
        if has_server_streaming:
            lines.append("import java.util.Iterator;")

        lines.append("")
        lines.append("/**")
        lines.append(f" * Auto-generated typed client for {interface.service_name}.")
        if interface.doc_comment:
            lines.append(" *")
            for doc_line in interface.doc_comment.strip().split("\n"):
                doc_line = doc_line.strip()
                if doc_line.startswith("//"):
                    doc_line = doc_line[2:].strip()
                lines.append(f" * <p>{doc_line}" if doc_line else " *")
        lines.append(" *")
        lines.append(" * <p>Do not edit — regenerate with {@code jmp codegen}.")
        lines.append(" */")
        lines.append(f"public class {client_name} {{")
        lines.append("")
        lines.append(
            f"    private final {grpc_class}.{interface.service_name}BlockingStub stub;"
        )
        lines.append("")

        # Constructor
        lines.append(f"    public {client_name}(ExporterSession session, String driverName) {{")
        lines.append(
            '        String uuid = session.getReport().findByName(driverName).getUuid();'
        )
        lines.append("        Channel channel = session.getChannel();")
        lines.append(
            f"        this.stub = {grpc_class}.newBlockingStub(channel)"
        )
        lines.append("            .withInterceptors(new UuidMetadataInterceptor(uuid));")
        lines.append("    }")
        lines.append("")

        # Methods
        for method in interface.methods:
            if method.stream_constructor:
                # Skip bidi/client-streaming methods — handled via StreamChannel
                continue

            method_java_name = _snake_to_camel(_pascal_to_snake(method.name))
            input_short = method.input_type.rsplit(".", 1)[-1]
            output_short = method.output_type.rsplit(".", 1)[-1]

            # Build parameter list from input message fields
            params, call_builder = _build_method_params(
                input_short, method, interface.messages
            )

            # Javadoc
            if method.doc_comment:
                lines.append(_wrap_javadoc(method.doc_comment))

            if method.server_streaming:
                lines.append(
                    f"    public Iterator<{output_short}> {method_java_name}({', '.join(params)}) {{"
                )
                lines.append(f"        return stub.{method_java_name}({call_builder});")
            else:
                if output_short == "Empty":
                    lines.append(
                        f"    public void {method_java_name}({', '.join(params)}) {{"
                    )
                    lines.append(f"        stub.{method_java_name}({call_builder});")
                else:
                    lines.append(
                        f"    public {output_short} {method_java_name}({', '.join(params)}) {{"
                    )
                    lines.append(
                        f"        return stub.{method_java_name}({call_builder});"
                    )
            lines.append("    }")
            lines.append("")

        lines.append("}")
        lines.append("")

        rel_path = f"{package_path}/{client_name}.java"
        return {rel_path: "\n".join(lines)}

    def generate_device_wrapper(self, ctx: CodegenContext) -> dict[str, str]:
        ec = ctx.exporter_class
        device_class = _kebab_to_pascal(ec.name) + "Device"
        package_name = ctx.package_name or "dev.jumpstarter.devices"
        package_path = package_name.replace(".", "/")

        lines: list[str] = []
        lines.append(f"package {package_name};")
        lines.append("")
        lines.append("import dev.jumpstarter.client.ExporterSession;")

        # Import per-interface client classes (external packages or generated stubs)
        for iface in ec.interfaces:
            fqn, _, _ = _resolve_java_import(iface)
            lines.append(f"import {fqn};")

        # Nullable annotation for optional interfaces
        has_optional = any(
            i.optionality == Optionality.OPTIONAL for i in ec.interfaces
        )
        if has_optional:
            lines.append("import org.jetbrains.annotations.Nullable;")

        lines.append("")
        lines.append("/**")
        lines.append(f" * Auto-generated typed wrapper for ExporterClass {ec.name}.")
        lines.append(" *")
        lines.append(
            " * <p>Do not edit — regenerate with {@code jmp codegen} when the ExporterClass changes."
        )
        lines.append(" */")
        lines.append(f"public class {device_class} implements AutoCloseable {{")
        lines.append("")

        # Fields
        for iface in ec.interfaces:
            _, client_name, _ = _resolve_java_import(iface)
            accessor = iface.name
            if iface.optionality == Optionality.OPTIONAL:
                lines.append(
                    f"    /** {_accessor_doc(iface)} */\n"
                    f"    @Nullable\n"
                    f"    private final {client_name} {accessor};"
                )
            else:
                lines.append(
                    f"    /** {_accessor_doc(iface)} */\n"
                    f"    private final {client_name} {accessor};"
                )
        lines.append("")

        # Constructor
        lines.append(f"    public {device_class}(ExporterSession session) {{")
        for iface in ec.interfaces:
            _, client_name, _ = _resolve_java_import(iface)
            accessor = iface.name
            if iface.optionality == Optionality.OPTIONAL:
                lines.append(
                    f'        this.{accessor} = session.hasDriver("{accessor}") '
                    f'? new {client_name}(session, "{accessor}") : null;'
                )
            else:
                lines.append(
                    f'        this.{accessor} = new {client_name}(session, "{accessor}");'
                )
        lines.append("    }")
        lines.append("")

        # Accessors
        for iface in ec.interfaces:
            _, client_name, _ = _resolve_java_import(iface)
            accessor = iface.name
            if iface.optionality == Optionality.OPTIONAL:
                lines.append(f"    @Nullable")
            lines.append(f"    public {client_name} {accessor}() {{ return {accessor}; }}")
            lines.append("")

        # AutoCloseable
        lines.append("    @Override")
        lines.append("    public void close() {")
        lines.append("        // Session cleanup is handled by ExporterSession")
        lines.append("    }")

        lines.append("}")
        lines.append("")

        rel_path = f"{package_path}/{device_class}.java"
        return {rel_path: "\n".join(lines)}

    def generate_test_fixture(self, ctx: CodegenContext) -> dict[str, str]:
        # The runtime JumpstarterExtension and @JumpstarterDevice handle everything.
        # No generated test fixture code is needed beyond the device wrapper itself,
        # since JUnit 5's reflection-based injection handles arbitrary device types.
        return {}

    def generate_package_metadata(self, ctx: CodegenContext) -> dict[str, str]:
        ec = ctx.exporter_class
        files: dict[str, str] = {}

        # Collect all proto packages referenced
        proto_packages = set()
        for iface in ec.interfaces:
            proto_packages.add(iface.proto_package)

        # Generate a build.gradle.kts for the generated code
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

        # Add external driver package dependencies
        for iface in ec.interfaces:
            hint = iface.drivers.get("java")
            if hint and hint.client_class and hint.package:
                # hint.package is a Gradle project path (e.g. ":java:jumpstarter-driver-network")
                # or a Maven coordinate (e.g. "dev.jumpstarter:driver-network:0.1.0")
                pkg = hint.package
                if pkg.startswith(":") or pkg.startswith("java:"):
                    # Gradle project reference
                    if not pkg.startswith(":"):
                        pkg = f":{pkg}"
                    lines.append(f'    implementation(project("{pkg}"))')
                else:
                    lines.append(f'    implementation("{pkg}")')

        lines.append('    implementation("io.grpc:grpc-netty-shaded:1.68.1")')
        lines.append('    implementation("io.grpc:grpc-protobuf:1.68.1")')
        lines.append('    implementation("io.grpc:grpc-stub:1.68.1")')
        lines.append('    implementation("com.google.protobuf:protobuf-java:4.28.3")')
        lines.append('    implementation("org.jetbrains:annotations:26.0.1")')
        lines.append('    compileOnly("org.apache.tomcat:annotations-api:6.0.53")')
        lines.append("")
        lines.append('    testImplementation("org.junit.jupiter:junit-jupiter:5.11.3")')
        lines.append('    testRuntimeOnly("org.junit.platform:junit-platform-launcher")')
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
        lines.append("tasks.test {")
        lines.append("    useJUnitPlatform()")
        lines.append("}")
        lines.append("")

        files["build.gradle.kts"] = "\n".join(lines)

        return files


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pascal_to_snake(name: str) -> str:
    """Convert PascalCase to snake_case."""
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    s = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s)
    return s.lower()


def _build_method_params(
    input_short: str,
    method: InterfaceMethod,
    messages: list[ProtoMessage],
) -> tuple[list[str], str]:
    """Build Java method parameters and call expression for a gRPC method.

    Returns (parameter_list, call_builder_expression).
    """
    if input_short == "Empty":
        return [], "Empty.getDefaultInstance()"

    # Find the input message
    input_msg = None
    for msg in messages:
        if msg.name == input_short:
            input_msg = msg
            break

    if input_msg is None or not input_msg.fields:
        return [], f"{input_short}.getDefaultInstance()"

    params: list[str] = []
    builder_calls: list[str] = []
    for field in input_msg.fields:
        java_type = _java_type_for_field(field)
        param_name = _snake_to_camel(field.name)

        if field.is_repeated:
            java_type = f"java.util.List<{_java_boxed_type(java_type)}>"
            setter = f"addAll{_snake_to_pascal(field.name)}"
        elif field.is_optional:
            java_type = _java_boxed_type(java_type)
            setter = f"set{_snake_to_pascal(field.name)}"
        else:
            setter = f"set{_snake_to_pascal(field.name)}"

        params.append(f"{java_type} {param_name}")
        builder_calls.append(f".{setter}({param_name})")

    builder_expr = (
        f"{input_short}.newBuilder()"
        + "".join(builder_calls)
        + ".build()"
    )

    return params, builder_expr


def _accessor_doc(iface: DriverInterfaceRef) -> str:
    """Generate a doc string for an accessor field."""
    kind = "required" if iface.optionality == Optionality.REQUIRED else "optional, may be null"
    client_name = _service_to_client_name(iface.service_name)
    return f"{client_name} — {kind} by ExporterClass"


register_language("java", JavaLanguageGenerator)
