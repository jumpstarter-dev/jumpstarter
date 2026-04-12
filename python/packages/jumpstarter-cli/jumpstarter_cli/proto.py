"""
jmp proto — export, check, and generate code from driver interface .proto definitions.
"""

import importlib
import os
import sys
import tempfile

import click
from google.protobuf.descriptor_pb2 import (
    DescriptorProto,
    EnumDescriptorProto,
    FieldDescriptorProto,
    FileDescriptorProto,
    FileDescriptorSet,
    MethodDescriptorProto,
)
from jumpstarter_cli_common.alias import AliasedGroup

# FileDescriptorProto field numbers (used to look up source_code_info comments)
_FDP_MESSAGE_TYPE = 4
_FDP_SERVICE = 6
_SDP_METHOD = 2
_DP_FIELD = 2


def _load_interface_class(interface: str) -> type:
    """Load an interface class from a dotted import path.

    Accepts either "package.module.ClassName" or "package.module:ClassName".
    """
    if ":" in interface:
        module_path, class_name = interface.rsplit(":", 1)
    elif "." in interface:
        module_path, class_name = interface.rsplit(".", 1)
    else:
        raise click.ClickException(
            f"Invalid interface path '{interface}'. "
            "Use 'package.module.ClassName' or 'package.module:ClassName'."
        )

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise click.ClickException(
            f"Could not import module '{module_path}': {e}"
        ) from e

    cls = getattr(module, class_name, None)
    if cls is None:
        raise click.ClickException(
            f"Class '{class_name}' not found in module '{module_path}'."
        )
    return cls


# ---------------------------------------------------------------------------
# Proto source renderer
# ---------------------------------------------------------------------------

# Map protobuf type enum to scalar type name
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


def _get_comment(fd: FileDescriptorProto, path: list[int]) -> str | None:
    """Look up leading comment from source_code_info for the given path."""
    if not fd.HasField("source_code_info"):
        return None
    for loc in fd.source_code_info.location:
        if list(loc.path) == path:
            text = loc.leading_comments.rstrip("\n")
            if text:
                return text
    return None


def _format_comment(comment: str, indent: str) -> str:
    """Format a comment as proto // comment lines."""
    lines = comment.split("\n")
    return "\n".join(f"{indent}// {line}" if line else f"{indent}//" for line in lines)


def _resolve_type_name(type_name: str, package: str) -> str:
    """Resolve a fully-qualified type name for display.

    Strips the package prefix for types in the same package.
    Handles well-known types (google.protobuf.*) specially.
    """
    if type_name.startswith("."):
        type_name = type_name[1:]

    # Well-known google types — keep fully qualified
    if type_name.startswith("google.protobuf."):
        return type_name

    # Same package — use short name
    if type_name.startswith(f"{package}."):
        return type_name[len(package) + 1 :]

    return type_name


def _render_enum(enum_desc: EnumDescriptorProto, indent: str) -> str:
    """Render an enum definition."""
    lines = [f"{indent}enum {enum_desc.name} {{"]
    for val in enum_desc.value:
        lines.append(f"{indent}  {val.name} = {val.number};")
    lines.append(f"{indent}}}")
    return "\n".join(lines)


def _render_field(
    field: FieldDescriptorProto, package: str, indent: str,
    fd: FileDescriptorProto | None = None, field_path: list[int] | None = None,
) -> str:
    """Render a single field definition."""
    parts = []

    # Field comment
    if fd is not None and field_path is not None:
        comment = _get_comment(fd, field_path)
        if comment:
            parts.append(_format_comment(comment, indent))

    # Determine type string
    if field.type in (
        FieldDescriptorProto.TYPE_MESSAGE,
        FieldDescriptorProto.TYPE_ENUM,
    ):
        type_str = _resolve_type_name(field.type_name, package)
    else:
        type_str = _SCALAR_TYPE_NAMES.get(field.type, "unknown")

    # Label prefix
    label_prefix = ""
    if field.label == FieldDescriptorProto.LABEL_REPEATED:
        label_prefix = "repeated "
    elif field.proto3_optional:
        label_prefix = "optional "

    parts.append(f"{indent}{label_prefix}{type_str} {field.name} = {field.number};")
    return "\n".join(parts)


def _render_message(
    msg: DescriptorProto, package: str, indent: str,
    fd: FileDescriptorProto | None = None, msg_path: list[int] | None = None,
) -> str:
    """Render a message definition."""
    lines = []

    # Message comment
    if fd is not None and msg_path is not None:
        comment = _get_comment(fd, msg_path)
        if comment:
            lines.append(_format_comment(comment, indent))

    lines.append(f"{indent}message {msg.name} {{")

    # Nested enums
    for enum_desc in msg.enum_type:
        lines.append(_render_enum(enum_desc, indent + "  "))

    # Nested messages
    for nested in msg.nested_type:
        lines.append(_render_message(nested, package, indent + "  "))

    # Fields
    for i, field in enumerate(msg.field):
        field_path = (msg_path or []) + [_DP_FIELD, i] if msg_path else None
        rendered = _render_field(field, package, indent + "  ", fd, field_path)
        lines.append(rendered)

    lines.append(f"{indent}}}")
    return "\n".join(lines)


def _render_method(
    method: MethodDescriptorProto, package: str, indent: str,
    fd: FileDescriptorProto | None = None, method_path: list[int] | None = None,
) -> str:
    """Render an rpc method definition."""
    lines = []

    # Method comment
    if fd is not None and method_path is not None:
        comment = _get_comment(fd, method_path)
        if comment:
            lines.append(_format_comment(comment, indent))

    input_type = _resolve_type_name(method.input_type, package)
    output_type = _resolve_type_name(method.output_type, package)

    client_stream = "stream " if method.client_streaming else ""
    server_stream = "stream " if method.server_streaming else ""

    lines.append(
        f"{indent}rpc {method.name}({client_stream}{input_type}) "
        f"returns ({server_stream}{output_type});"
    )
    return "\n".join(lines)


def render_proto_source(fd: FileDescriptorProto) -> str:
    """Render a FileDescriptorProto as human-readable .proto source text."""
    lines: list[str] = []

    # Generated file header
    lines.append("// Code generated by jmp interface generate. DO NOT EDIT.")
    lines.append("")

    # Syntax
    lines.append(f'syntax = "{fd.syntax or "proto3"}";')
    lines.append(f"package {fd.package};")
    lines.append("")

    # Imports
    for dep in fd.dependency:
        lines.append(f'import "{dep}";')

    if fd.dependency:
        lines.append("")

    # Messages (top-level, before service for readability)
    # Actually proto convention puts service first, then messages.
    # Let's follow the JEP example: service first, then messages.

    # Services
    for svc_idx, service in enumerate(fd.service):
        svc_comment = _get_comment(fd, [_FDP_SERVICE, svc_idx])
        if svc_comment:
            lines.append(_format_comment(svc_comment, ""))
        lines.append(f"service {service.name} {{")

        # Methods
        for m_idx, method in enumerate(service.method):
            method_path = [_FDP_SERVICE, svc_idx, _SDP_METHOD, m_idx]
            rendered = _render_method(method, fd.package, "  ", fd, method_path)
            if m_idx > 0 or _get_comment(fd, method_path):
                lines.append("")
            lines.append(rendered)

        lines.append("}")

    # Messages
    for msg_idx, msg in enumerate(fd.message_type):
        lines.append("")
        msg_path = [_FDP_MESSAGE_TYPE, msg_idx]
        lines.append(_render_message(msg, fd.package, "", fd, msg_path))

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

@click.group(cls=AliasedGroup)
def proto():
    """Manage driver interface proto definitions."""


@proto.command("export")
@click.option(
    "--interface", "-i",
    required=True,
    help="Dotted import path of the DriverInterface class (e.g., jumpstarter_driver_power.driver.PowerInterface).",
)
@click.option(
    "--version", "-v",
    default="v1",
    show_default=True,
    help="Version string for the proto package.",
)
@click.option(
    "--output", "-o",
    type=click.Path(),
    default=None,
    help="Output file path. Writes to stdout if not specified.",
)
def export_proto(interface: str, version: str, output: str | None):
    """Export a .proto definition from a Python DriverInterface class."""
    cls = _load_interface_class(interface)
    proto_source = _generate_proto_source(cls, version)

    if output:
        with open(output, "w") as f:
            f.write(proto_source)
        click.echo(f"Generated {output}")
    else:
        click.echo(proto_source)


def _generate_proto_source(cls: type, version: str) -> str:
    """Build and render proto source for an interface class."""
    from jumpstarter.driver.descriptor_builder import build_file_descriptor

    fd = build_file_descriptor(cls, version=version)
    return render_proto_source(fd)



# ---------------------------------------------------------------------------
# Proto file parsing (via grpcio-tools)
# ---------------------------------------------------------------------------

def _parse_proto_file(
    proto_path: str,
    include_paths: list[str] | None = None,
) -> FileDescriptorProto:
    """Parse a .proto file into a FileDescriptorProto using grpcio-tools.

    grpcio-tools bundles protoc and well-known types (google/protobuf/*.proto),
    so no external binary is required.
    """
    import grpc_tools
    from grpc_tools import protoc as grpc_protoc

    proto_path = os.path.abspath(proto_path)
    proto_dir = os.path.dirname(proto_path)
    proto_name = os.path.basename(proto_path)

    paths = [proto_dir]
    if include_paths:
        paths.extend(include_paths)

    # grpc_tools bundles well-known types (google/protobuf/*.proto)
    wkt_path = os.path.join(os.path.dirname(grpc_tools.__file__), "_proto")
    if os.path.isdir(wkt_path):
        paths.append(wkt_path)

    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        args = ["grpc_tools.protoc"]
        for p in paths:
            args.append(f"-I{p}")
        args.extend([
            f"--descriptor_set_out={tmp_path}",
            "--include_source_info",
            proto_name,
        ])

        rc = grpc_protoc.main(args)
        if rc != 0:
            raise click.ClickException(f"protoc failed with exit code {rc}")

        with open(tmp_path, "rb") as f:
            data = f.read()
        if not data:
            raise click.ClickException("protoc produced empty output")

        fds = FileDescriptorSet.FromString(data)
        if not fds.file:
            raise click.ClickException("protoc produced empty descriptor set")

        return fds.file[-1]
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Descriptor comparison
# ---------------------------------------------------------------------------

class CheckResult:
    """Accumulates check findings across tiers."""

    def __init__(self):
        self.structural: list[str] = []  # Must match — exit 1
        self.contract: list[str] = []    # --strict — exit 2
        self.docs: list[str] = []        # --check-docs — informational

    @property
    def ok(self) -> bool:
        return not self.structural

    @property
    def strict_ok(self) -> bool:
        return not self.structural and not self.contract

    def exit_code(self, strict: bool = False, check_docs: bool = False) -> int:
        if self.structural:
            return 1
        if strict and self.contract:
            return 2
        return 0


def _normalize_type(type_name: str, package: str) -> str:
    """Normalize a type name for comparison (strip leading dot, resolve package)."""
    if type_name.startswith("."):
        type_name = type_name[1:]
    return type_name


def _compare_descriptors(
    python_fd: FileDescriptorProto,
    proto_fd: FileDescriptorProto,
    result: CheckResult,
    check_docs: bool = False,
) -> None:
    """Compare two FileDescriptorProtos and populate result."""
    # Service count
    if len(python_fd.service) != len(proto_fd.service):
        result.structural.append(
            f"Service count mismatch: Python has {len(python_fd.service)}, "
            f"proto has {len(proto_fd.service)}"
        )
        return

    if not python_fd.service or not proto_fd.service:
        return

    py_svc = python_fd.service[0]
    pr_svc = proto_fd.service[0]

    # Service name
    if py_svc.name != pr_svc.name:
        result.structural.append(
            f"Service name mismatch: Python='{py_svc.name}', proto='{pr_svc.name}'"
        )

    # Build method maps
    py_methods = {m.name: m for m in py_svc.method}
    pr_methods = {m.name: m for m in pr_svc.method}

    # Methods only in Python
    for name in sorted(set(py_methods) - set(pr_methods)):
        result.structural.append(f"Method '{name}' exists in Python but not in proto")

    # Methods only in proto
    for name in sorted(set(pr_methods) - set(py_methods)):
        result.structural.append(f"Method '{name}' exists in proto but not in Python")

    # Compare common methods
    py_pkg = python_fd.package
    pr_pkg = proto_fd.package
    for name in sorted(set(py_methods) & set(pr_methods)):
        py_m = py_methods[name]
        pr_m = pr_methods[name]

        # Streaming flags
        if py_m.server_streaming != pr_m.server_streaming:
            result.structural.append(
                f"Method '{name}': server_streaming mismatch "
                f"(Python={py_m.server_streaming}, proto={pr_m.server_streaming})"
            )
        if py_m.client_streaming != pr_m.client_streaming:
            result.structural.append(
                f"Method '{name}': client_streaming mismatch "
                f"(Python={py_m.client_streaming}, proto={pr_m.client_streaming})"
            )

        # Input/output types (normalize for comparison)
        py_input = _normalize_type(py_m.input_type, py_pkg)
        pr_input = _normalize_type(pr_m.input_type, pr_pkg)
        # Replace package prefix to compare structurally
        py_input_short = py_input.replace(f"{py_pkg}.", "")
        pr_input_short = pr_input.replace(f"{pr_pkg}.", "")
        if py_input_short != pr_input_short:
            result.structural.append(
                f"Method '{name}': input type mismatch "
                f"(Python='{py_input}', proto='{pr_input}')"
            )

        py_output = _normalize_type(py_m.output_type, py_pkg)
        pr_output = _normalize_type(pr_m.output_type, pr_pkg)
        py_output_short = py_output.replace(f"{py_pkg}.", "")
        pr_output_short = pr_output.replace(f"{pr_pkg}.", "")
        if py_output_short != pr_output_short:
            result.structural.append(
                f"Method '{name}': output type mismatch "
                f"(Python='{py_output}', proto='{pr_output}')"
            )

    # Compare messages
    py_msgs = {m.name: m for m in python_fd.message_type}
    pr_msgs = {m.name: m for m in proto_fd.message_type}

    for name in sorted(set(py_msgs) - set(pr_msgs)):
        result.contract.append(f"Message '{name}' exists in Python but not in proto")
    for name in sorted(set(pr_msgs) - set(py_msgs)):
        result.contract.append(f"Message '{name}' exists in proto but not in Python")

    for name in sorted(set(py_msgs) & set(pr_msgs)):
        _compare_messages(py_msgs[name], pr_msgs[name], name, result)

    # Compare doc comments if requested
    if check_docs:
        _compare_docs(python_fd, proto_fd, result)


def _compare_messages(
    py_msg: DescriptorProto,
    pr_msg: DescriptorProto,
    msg_name: str,
    result: CheckResult,
) -> None:
    """Compare two message descriptors."""
    py_fields = {f.name: f for f in py_msg.field}
    pr_fields = {f.name: f for f in pr_msg.field}

    for fname in sorted(set(py_fields) - set(pr_fields)):
        result.structural.append(
            f"Message '{msg_name}': field '{fname}' exists in Python but not in proto"
        )
    for fname in sorted(set(pr_fields) - set(py_fields)):
        result.structural.append(
            f"Message '{msg_name}': field '{fname}' exists in proto but not in Python"
        )

    for fname in sorted(set(py_fields) & set(pr_fields)):
        py_f = py_fields[fname]
        pr_f = pr_fields[fname]

        if py_f.type != pr_f.type:
            py_type = _SCALAR_TYPE_NAMES.get(py_f.type, f"type({py_f.type})")
            pr_type = _SCALAR_TYPE_NAMES.get(pr_f.type, f"type({pr_f.type})")
            result.structural.append(
                f"Message '{msg_name}', field '{fname}': type mismatch "
                f"(Python={py_type}, proto={pr_type})"
            )

        if py_f.number != pr_f.number:
            result.structural.append(
                f"Message '{msg_name}', field '{fname}': field number mismatch "
                f"(Python={py_f.number}, proto={pr_f.number})"
            )

        if py_f.label != pr_f.label:
            result.contract.append(
                f"Message '{msg_name}', field '{fname}': label mismatch "
                f"(Python={py_f.label}, proto={pr_f.label})"
            )


def _compare_docs(
    python_fd: FileDescriptorProto,
    proto_fd: FileDescriptorProto,
    result: CheckResult,
) -> None:
    """Compare doc comments between two descriptors."""
    py_comments = {}
    if python_fd.HasField("source_code_info"):
        for loc in python_fd.source_code_info.location:
            if loc.leading_comments:
                py_comments[tuple(loc.path)] = loc.leading_comments.strip()

    pr_comments = {}
    if proto_fd.HasField("source_code_info"):
        for loc in proto_fd.source_code_info.location:
            if loc.leading_comments:
                pr_comments[tuple(loc.path)] = loc.leading_comments.strip()

    # Check for doc comment drift on common paths
    for path in sorted(set(py_comments) & set(pr_comments)):
        if py_comments[path] != pr_comments[path]:
            result.docs.append(
                f"Doc comment drift at path {list(path)}: "
                f"Python and proto comments differ"
            )

    # Comments only in one side
    for path in sorted(set(py_comments) - set(pr_comments)):
        result.docs.append(f"Doc comment at path {list(path)} exists in Python but not proto")
    for path in sorted(set(pr_comments) - set(py_comments)):
        result.docs.append(f"Doc comment at path {list(path)} exists in proto but not Python")


# ---------------------------------------------------------------------------
# Check CLI commands
# ---------------------------------------------------------------------------

@proto.command()
@click.option(
    "--proto",
    required=True,
    type=click.Path(exists=True),
    help="Path to the .proto file to check against.",
)
@click.option(
    "--interface", "-i",
    required=True,
    help="Dotted import path of the DriverInterface class.",
)
@click.option(
    "--version", "-v",
    default="v1",
    show_default=True,
    help="Version string for the proto package.",
)
@click.option(
    "--proto-path", "-I",
    multiple=True,
    help="Additional include paths for protoc (for resolving imports).",
)
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Fail on contract-level differences (message structure). Exit code 2.",
)
@click.option(
    "--check-docs",
    is_flag=True,
    default=False,
    help="Check doc comment consistency between Python and proto.",
)
def check(
    proto: str,
    interface: str,
    version: str,
    proto_path: tuple[str, ...],
    strict: bool,
    check_docs: bool,
):
    """Check consistency between a .proto file and a Python DriverInterface.

    Compares the FileDescriptorProto built from the Python class against the
    one parsed from the .proto file. Reports mismatches at three tiers:

    \b
    - Structural (exit 1): service name, methods, streaming flags, types
    - Contract (exit 2 with --strict): message fields, labels
    - Docs (informational with --check-docs): comment drift
    """
    from jumpstarter.driver.descriptor_builder import build_file_descriptor

    cls = _load_interface_class(interface)
    python_fd = build_file_descriptor(cls, version=version)
    proto_fd = _parse_proto_file(proto, list(proto_path) if proto_path else None)

    result = CheckResult()
    _compare_descriptors(python_fd, proto_fd, result, check_docs=check_docs)

    # Report findings
    if result.structural:
        click.secho("STRUCTURAL mismatches (must fix):", fg="red", bold=True)
        for msg in result.structural:
            click.echo(f"  ✗ {msg}")

    if result.contract:
        label = "CONTRACT" if strict else "contract"
        color = "red" if strict else "yellow"
        click.secho(f"\n{label} differences:", fg=color, bold=strict)
        for msg in result.contract:
            click.echo(f"  {'✗' if strict else '⚠'} {msg}")

    if check_docs and result.docs:
        click.secho("\nDoc comment differences:", fg="yellow")
        for msg in result.docs:
            click.echo(f"  ⚠ {msg}")

    exit_code = result.exit_code(strict=strict, check_docs=check_docs)

    if exit_code == 0:
        click.secho("OK — proto and Python interface are consistent.", fg="green")
    else:
        click.echo("")

    sys.exit(exit_code)


# ---------------------------------------------------------------------------
# jmp proto generate — .proto → Python code generation
# ---------------------------------------------------------------------------

# Protobuf field type → Python type annotation string
_PROTO_TO_PYTHON_TYPE = {
    FieldDescriptorProto.TYPE_DOUBLE: "float",
    FieldDescriptorProto.TYPE_FLOAT: "float",
    FieldDescriptorProto.TYPE_INT64: "int",
    FieldDescriptorProto.TYPE_UINT64: "int",
    FieldDescriptorProto.TYPE_INT32: "int",
    FieldDescriptorProto.TYPE_FIXED64: "int",
    FieldDescriptorProto.TYPE_FIXED32: "int",
    FieldDescriptorProto.TYPE_BOOL: "bool",
    FieldDescriptorProto.TYPE_STRING: "str",
    FieldDescriptorProto.TYPE_BYTES: "bytes",
    FieldDescriptorProto.TYPE_UINT32: "int",
    FieldDescriptorProto.TYPE_SFIXED32: "int",
    FieldDescriptorProto.TYPE_SFIXED64: "int",
    FieldDescriptorProto.TYPE_SINT32: "int",
    FieldDescriptorProto.TYPE_SINT64: "int",
}


def _pascal_to_snake(name: str) -> str:
    """Convert PascalCase to snake_case."""
    result = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0:
            if name[i - 1].islower() or (i + 1 < len(name) and name[i + 1].islower()):
                result.append("_")
        result.append(ch.lower())
    return "".join(result)


def _get_python_field_type(
    field: FieldDescriptorProto,
    package: str,
    messages_by_name: dict[str, DescriptorProto],
) -> str:
    """Convert a proto field to its Python type string."""
    if field.type in (FieldDescriptorProto.TYPE_MESSAGE, FieldDescriptorProto.TYPE_ENUM):
        type_name = field.type_name
        if type_name.startswith("."):
            type_name = type_name[1:]
        if type_name.startswith(f"{package}."):
            type_name = type_name[len(package) + 1:]
        if type_name == "google.protobuf.Empty":
            return "None"
        if type_name == "google.protobuf.Value":
            return "Any"
        py_type = type_name
    else:
        py_type = _PROTO_TO_PYTHON_TYPE.get(field.type, "Any")

    if field.label == FieldDescriptorProto.LABEL_REPEATED:
        return f"list[{py_type}]"
    if field.proto3_optional:
        return f"{py_type} | None"
    return py_type


def _is_data_message(
    msg: DescriptorProto,
    rpc_types: set[str],
    service_methods: list[MethodDescriptorProto],
) -> bool:
    """Determine if a message is a data model vs a request/response wrapper."""
    if msg.name not in rpc_types:
        return True
    if any(
        m.output_type.endswith(f".{msg.name}") and m.server_streaming
        for m in service_methods
    ):
        return True
    if len(msg.field) > 1 or (len(msg.field) == 1 and msg.field[0].name != "value"):
        return True
    return False


def _collect_data_models(
    fd: FileDescriptorProto,
) -> tuple[dict[str, DescriptorProto], list[DescriptorProto], list[str]]:
    """Collect messages by name, data models, and data model names."""
    service = fd.service[0] if fd.service else None
    methods = list(service.method) if service else []

    messages_by_name: dict[str, DescriptorProto] = {}
    for msg in fd.message_type:
        messages_by_name[msg.name] = msg

    rpc_types: set[str] = set()
    if service:
        for method in methods:
            for type_name in (method.input_type, method.output_type):
                rpc_types.add(type_name.rsplit(".", 1)[-1])

    data_messages = [m for m in fd.message_type if _is_data_message(m, rpc_types, methods)]
    data_model_names = [m.name for m in data_messages]

    return messages_by_name, data_messages, data_model_names


def _resolve_return_type(
    output_short: str,
    messages_by_name: dict[str, DescriptorProto],
    data_model_names: list[str],
    package: str,
) -> str:
    """Resolve the Python return type from a proto output type."""
    if output_short == "Empty":
        return "None"
    if output_short in messages_by_name:
        resp_msg = messages_by_name[output_short]
        if output_short in data_model_names:
            return output_short
        if len(resp_msg.field) == 1 and resp_msg.field[0].name == "value":
            return _get_python_field_type(resp_msg.field[0], package, messages_by_name)
        return output_short
    return "Any"


def _gen_interface_py(
    fd: FileDescriptorProto, output_package: str,
) -> str:
    """Generate interface.py from a FileDescriptorProto."""
    package = fd.package
    service = fd.service[0] if fd.service else None
    messages_by_name, data_messages, data_model_names = _collect_data_models(fd)

    lines = [
        '"""Auto-generated interface from proto definition.',
        "",
        "Do not edit — regenerate with `jmp interface implement`.",
        '"""',
        "",
        "from abc import abstractmethod",
    ]

    needs_async_generator = service and any(m.server_streaming for m in service.method)
    needs_base_model = bool(data_messages)
    needs_str_enum = bool(fd.enum_type) or any(m.enum_type for m in fd.message_type)
    needs_any = any(
        f.type == FieldDescriptorProto.TYPE_MESSAGE and "google.protobuf.Value" in f.type_name
        for m in fd.message_type for f in m.field
    )

    if needs_async_generator:
        lines.append("from collections.abc import AsyncGenerator")
    if needs_any:
        lines.append("from typing import Any")
    if needs_base_model:
        lines.append("from pydantic import BaseModel")
    if needs_str_enum:
        lines.append("from enum import StrEnum")
    lines.append("from jumpstarter.driver import DriverInterface")
    lines.append("")

    # Enums
    all_enums = list(fd.enum_type)
    for msg in fd.message_type:
        all_enums.extend(msg.enum_type)
    for enum_desc in all_enums:
        lines.append("")
        lines.append(f"class {enum_desc.name}(StrEnum):")
        for val in enum_desc.value:
            if val.number == 0 and val.name.endswith("_UNSPECIFIED"):
                continue
            val_name = val.name
            prefix = enum_desc.name.upper() + "_"
            if val_name.startswith(prefix):
                val_name = val_name[len(prefix):]
            lines.append(f'    {val_name} = "{val_name.lower()}"')
        lines.append("")

    # Data model classes
    for msg in data_messages:
        lines.append("")
        lines.append(f"class {msg.name}(BaseModel):")
        if not msg.field:
            lines.append("    pass")
        else:
            for f in msg.field:
                py_type = _get_python_field_type(f, package, messages_by_name)
                default = " = None" if f.proto3_optional else ""
                lines.append(f"    {f.name}: {py_type}{default}")
        lines.append("")

    # Interface class
    if service:
        svc_comment = _get_comment(fd, [_FDP_SERVICE, 0])
        lines.append("")
        lines.append(f"class {service.name}(DriverInterface):")
        if svc_comment:
            lines.append(f'    """{svc_comment}"""')
            lines.append("")

        client_name = service.name.replace("Interface", "Client")
        lines.append("    @classmethod")
        lines.append("    def client(cls) -> str:")
        lines.append(f'        return "{output_package}.client.{client_name}"')
        lines.append("")

        for method in service.method:
            method_name = _pascal_to_snake(method.name)
            params: list[str] = ["self"]
            input_short = method.input_type.rsplit(".", 1)[-1]
            if input_short != "Empty" and input_short in messages_by_name:
                for f in messages_by_name[input_short].field:
                    py_type = _get_python_field_type(f, package, messages_by_name)
                    default = " = None" if f.proto3_optional else ""
                    params.append(f"{f.name}: {py_type}{default}")

            output_short = method.output_type.rsplit(".", 1)[-1]
            return_type = _resolve_return_type(
                output_short, messages_by_name, data_model_names, package
            )
            if method.server_streaming:
                return_type = f"AsyncGenerator[{return_type}, None]"

            lines.append("    @abstractmethod")
            lines.append(f"    async def {method_name}({', '.join(params)}) -> {return_type}: ...")
            lines.append("")

    lines.append("")
    return "\n".join(lines)


def _gen_client_py(fd: FileDescriptorProto, output_package: str) -> str:
    """Generate client.py from a FileDescriptorProto."""
    package = fd.package
    service = fd.service[0] if fd.service else None
    if not service:
        return ""

    messages_by_name, _, data_model_names = _collect_data_models(fd)
    client_name = service.name.replace("Interface", "Client")
    needs_generator = any(m.server_streaming for m in service.method)

    lines = [
        '"""Auto-generated client for the interface.',
        "",
        "Do not edit — regenerate with `jmp interface implement`.",
        '"""',
        "",
    ]
    if needs_generator:
        lines.append("from collections.abc import Generator")
    lines.append("from jumpstarter.client import DriverClient")

    imports = [service.name] + data_model_names
    lines.append(f"from .interface import {', '.join(imports)}")
    lines.append("")
    lines.append("")
    lines.append(f"class {client_name}({service.name}, DriverClient):")
    lines.append(f'    """Auto-generated client for {service.name}."""')
    lines.append("")

    for method in service.method:
        method_name = _pascal_to_snake(method.name)
        params: list[str] = ["self"]
        param_names: list[str] = []
        input_short = method.input_type.rsplit(".", 1)[-1]
        if input_short != "Empty" and input_short in messages_by_name:
            for f in messages_by_name[input_short].field:
                py_type = _get_python_field_type(f, package, messages_by_name)
                default = " = None" if f.proto3_optional else ""
                params.append(f"{f.name}: {py_type}{default}")
                param_names.append(f.name)

        output_short = method.output_type.rsplit(".", 1)[-1]
        return_type = _resolve_return_type(
            output_short, messages_by_name, data_model_names, package
        )
        is_model = return_type in data_model_names
        args_str = ", ".join(param_names)

        if method.server_streaming:
            lines.append(f"    def {method_name}({', '.join(params)}) -> Generator[{return_type}, None, None]:")
            call_extra = f", {args_str}" if args_str else ""
            if is_model:
                lines.append(f'        for raw in self.streamingcall("{method_name}"{call_extra}):')
                lines.append(f"            yield {return_type}.model_validate(raw, strict=True)")
            else:
                lines.append(f'        yield from self.streamingcall("{method_name}"{call_extra})')
        else:
            lines.append(f"    def {method_name}({', '.join(params)}) -> {return_type}:")
            call_args = f'"{method_name}"'
            if args_str:
                call_args += f", {args_str}"
            if is_model:
                lines.append(f"        return {return_type}.model_validate(self.call({call_args}), strict=True)")
            elif return_type == "None":
                lines.append(f"        self.call({call_args})")
            else:
                lines.append(f"        return self.call({call_args})")
        lines.append("")

    lines.append("")
    return "\n".join(lines)


def _gen_driver_py(fd: FileDescriptorProto, output_package: str) -> str:
    """Generate driver.py (adapter) from a FileDescriptorProto."""
    package = fd.package
    service = fd.service[0] if fd.service else None
    if not service:
        return ""

    messages_by_name, _, data_model_names = _collect_data_models(fd)
    driver_name = service.name.replace("Interface", "Driver")
    needs_async_generator = any(m.server_streaming for m in service.method)

    lines = [
        '"""Auto-generated driver adapter for the interface.',
        "",
        "Subclass this and implement the abstract _methods.",
        "Do not edit — regenerate with `jmp interface implement`.",
        '"""',
        "",
        "from abc import abstractmethod",
    ]
    if needs_async_generator:
        lines.append("from collections.abc import AsyncGenerator")
    lines.append("from jumpstarter.driver import Driver, export")

    imports = [service.name] + data_model_names
    lines.append(f"from .interface import {', '.join(imports)}")
    lines.append("")
    lines.append("")
    lines.append(f"class {driver_name}({service.name}, Driver):")
    lines.append(f'    """Auto-generated driver adapter for {service.name}.')
    lines.append("")
    lines.append("    Subclass this and implement the abstract methods with your")
    lines.append("    hardware-specific logic.")
    lines.append('    """')
    lines.append("")

    for method in service.method:
        method_name = _pascal_to_snake(method.name)
        params: list[str] = ["self"]
        param_names: list[str] = []
        input_short = method.input_type.rsplit(".", 1)[-1]
        if input_short != "Empty" and input_short in messages_by_name:
            for f in messages_by_name[input_short].field:
                py_type = _get_python_field_type(f, package, messages_by_name)
                default = " = None" if f.proto3_optional else ""
                params.append(f"{f.name}: {py_type}{default}")
                param_names.append(f.name)

        output_short = method.output_type.rsplit(".", 1)[-1]
        return_type = _resolve_return_type(
            output_short, messages_by_name, data_model_names, package
        )
        call_args = ", ".join(param_names)

        if method.server_streaming:
            lines.append("    @export")
            lines.append(f"    async def {method_name}({', '.join(params)}) -> AsyncGenerator[{return_type}, None]:")
            lines.append(f"        async for item in self._{method_name}({call_args}):")
            lines.append("            yield item")
        else:
            lines.append("    @export")
            lines.append(f"    async def {method_name}({', '.join(params)}) -> {return_type}:")
            if return_type == "None":
                lines.append(f"        await self._{method_name}({call_args})")
            else:
                lines.append(f"        return await self._{method_name}({call_args})")
        lines.append("")

    lines.append("    # ── Abstract methods for driver implementors ──────────────")
    lines.append("")

    for method in service.method:
        method_name = _pascal_to_snake(method.name)
        params = ["self"]
        input_short = method.input_type.rsplit(".", 1)[-1]
        if input_short != "Empty" and input_short in messages_by_name:
            for f in messages_by_name[input_short].field:
                py_type = _get_python_field_type(f, package, messages_by_name)
                default = " = None" if f.proto3_optional else ""
                params.append(f"{f.name}: {py_type}{default}")

        output_short = method.output_type.rsplit(".", 1)[-1]
        return_type = _resolve_return_type(
            output_short, messages_by_name, data_model_names, package
        )
        if method.server_streaming:
            return_type = f"AsyncGenerator[{return_type}, None]"

        lines.append("    @abstractmethod")
        lines.append(f"    async def _{method_name}({', '.join(params)}) -> {return_type}: ...")
        lines.append("")

    lines.append("")
    return "\n".join(lines)


def _gen_init_py(fd: FileDescriptorProto, output_package: str) -> str:
    """Generate __init__.py with re-exports."""
    service = fd.service[0] if fd.service else None
    if not service:
        return ""

    _, _, data_model_names = _collect_data_models(fd)
    client_name = service.name.replace("Interface", "Client")
    driver_name = service.name.replace("Interface", "Driver")

    exports = [service.name] + data_model_names + [client_name, driver_name]
    lines = [f"from .interface import {service.name}"]
    for name in data_model_names:
        lines.append(f"from .interface import {name}")
    lines.append(f"from .client import {client_name}")
    lines.append(f"from .driver import {driver_name}")
    lines.append("")
    lines.append(f"__all__ = {exports!r}")
    lines.append("")
    return "\n".join(lines)


def _gen_servicer_py(fd: FileDescriptorProto, output_package: str) -> str:
    """Generate servicer.py — a gRPC servicer adapter that bridges proto RPCs to driver methods."""
    package = fd.package
    service = fd.service[0] if fd.service else None
    if not service:
        return ""

    messages_by_name, _, data_model_names = _collect_data_models(fd)

    # Derive import paths from proto package: jumpstarter.interfaces.{name}.{version}
    parts = package.split(".")
    if len(parts) >= 4 and parts[0] == "jumpstarter" and parts[1] == "interfaces":
        interface_name = parts[2]
        version = parts[3]
    else:
        interface_name = parts[-2] if len(parts) >= 2 else parts[-1]
        version = parts[-1] if len(parts) >= 2 else "v1"

    proto_filename = fd.name.rsplit("/", 1)[-1].replace(".proto", "")
    service_name = f"{package}.{service.name}"

    # Determine if we need specific imports
    needs_empty = any(
        m.input_type.endswith(".Empty") or m.output_type.endswith(".Empty")
        for m in service.method
    )
    has_streaming = any(m.server_streaming for m in service.method)
    has_bidi = any(m.client_streaming for m in service.method)

    lines = [
        f'"""Auto-generated gRPC servicer adapter for {service.name}.',
        "",
        "Bridges native gRPC calls to @export driver methods via DriverRegistry.",
        "Do not edit — regenerate with `jmp interface implement`.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
    ]

    inspect_imports = []
    if any(not m.client_streaming and not m.server_streaming for m in service.method):
        inspect_imports.append("iscoroutinefunction")
    if has_streaming:
        inspect_imports.append("isasyncgenfunction")
    if inspect_imports:
        lines.append(f"from inspect import {', '.join(sorted(inspect_imports))}")
        lines.append("")

    has_stream_data_bidi = any(
        m.client_streaming and m.input_type.endswith(".StreamData")
        for m in service.method
    )

    lines.append("import grpc")
    if has_stream_data_bidi:
        lines.append("import anyio")
    if needs_empty:
        lines.append("from google.protobuf.empty_pb2 import Empty")
    lines.append("")
    # Relative import — servicer.py lives alongside pb2/pb2_grpc in the same directory
    lines.append(f"from . import {proto_filename}_pb2, {proto_filename}_pb2_grpc")
    lines.append("")
    lines.append("from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter")
    lines.append("")
    lines.append(f'SERVICE_NAME = "{service_name}"')
    lines.append("")
    lines.append("")

    # _register() function
    lines.append("def _register():")
    lines.append(f'    """Register the {service.name} servicer adapter."""')
    lines.append(f"    from {output_package}.driver import {service.name}")
    lines.append("")
    lines.append("    register_servicer_adapter(")
    lines.append(f"        interface_class={service.name},")
    lines.append(f"        service_name=SERVICE_NAME,")
    lines.append(f"        servicer_factory={service.name}Servicer,")
    lines.append(f"        add_to_server={proto_filename}_pb2_grpc.add_{service.name}Servicer_to_server,")
    lines.append("    )")
    lines.append("")
    lines.append("")

    # Servicer class
    lines.append(f"class {service.name}Servicer({proto_filename}_pb2_grpc.{service.name}Servicer):")
    lines.append(f'    """gRPC servicer that bridges {service.name} to @export driver methods."""')
    lines.append("")
    lines.append("    def __init__(self, registry: DriverRegistry):")
    lines.append("        self._registry = registry")

    for method in service.method:
        lines.append("")
        _gen_servicer_method(
            lines, method, messages_by_name, data_model_names,
            proto_filename, package,
        )

    lines.append("")
    lines.append("")
    lines.append("# Register the adapter at import time so the Session can discover it.")
    lines.append("_register()")
    lines.append("")
    return "\n".join(lines)


def _gen_servicer_method(
    lines: list[str],
    method: MethodDescriptorProto,
    messages_by_name: dict[str, DescriptorProto],
    data_model_names: list[str],
    proto_filename: str,
    package: str,
) -> None:
    """Generate a single servicer method."""
    method_name = _pascal_to_snake(method.name)
    proto_method_name = method.name

    input_short = method.input_type.rsplit(".", 1)[-1]
    output_short = method.output_type.rsplit(".", 1)[-1]
    is_empty_input = input_short == "Empty"
    is_empty_output = output_short == "Empty"

    # Bidi streaming with StreamData → native @exportstream byte forwarding
    if method.client_streaming and input_short == "StreamData":
        lines.append(f"    async def {proto_method_name}(self, request_iterator, context):")
        lines.append(f"        driver = await self._registry.resolve(context, SERVICE_NAME)")
        lines.append(f"        async with driver.{method_name}() as stream:")
        lines.append(f"            async def _inbound():")
        lines.append(f"                async for msg in request_iterator:")
        lines.append(f"                    await stream.send(msg.payload)")
        lines.append(f"                await stream.send_eof()")
        lines.append(f"            async with anyio.create_task_group() as tg:")
        lines.append(f"                tg.start_soon(_inbound)")
        lines.append(f"                try:")
        lines.append(f"                    while True:")
        lines.append(f"                        data = await stream.receive()")
        lines.append(f"                        yield {proto_filename}_pb2.StreamData(payload=data)")
        lines.append(f"                except (anyio.EndOfStream, anyio.ClosedResourceError):")
        lines.append(f"                    pass")
        lines.append(f"                tg.cancel_scope.cancel()")
        return

    # Bidi or client-streaming with other types → UNIMPLEMENTED
    if method.client_streaming:
        lines.append(f"    async def {proto_method_name}(self, request_iterator, context):")
        lines.append(f"        await context.abort(")
        lines.append(f"            grpc.StatusCode.UNIMPLEMENTED,")
        lines.append(f'            "{proto_method_name} bidi streaming not implemented for native gRPC",')
        lines.append(f"        )")
        return

    # Build driver call arguments from request fields
    call_args: list[str] = []
    if not is_empty_input and input_short in messages_by_name:
        for f in messages_by_name[input_short].field:
            call_args.append(f"request.{f.name}")
    args_str = ", ".join(call_args)

    if method.server_streaming:
        # Server-streaming RPC
        lines.append(f"    async def {proto_method_name}(self, request, context):")
        lines.append(f"        driver = await self._registry.resolve(context, SERVICE_NAME)")

        response_expr = _build_servicer_response_expr(
            output_short, messages_by_name, data_model_names, proto_filename, "item",
        )

        # Async branch
        lines.append(f"        if isasyncgenfunction(type(driver).{method_name}):")
        lines.append(f"            async for item in driver.{method_name}({args_str}):")
        lines.append(f"                yield {response_expr}")
        # Sync branch
        lines.append(f"        else:")
        lines.append(f"            for item in driver.{method_name}({args_str}):")
        lines.append(f"                yield {response_expr}")
    else:
        # Unary RPC
        lines.append(f"    async def {proto_method_name}(self, request, context):")
        lines.append(f"        driver = await self._registry.resolve(context, SERVICE_NAME)")

        call_expr = f"driver.{method_name}({args_str})"

        if is_empty_output:
            # void return
            lines.append(f"        if iscoroutinefunction(driver.{method_name}):")
            lines.append(f"            await {call_expr}")
            lines.append(f"        else:")
            lines.append(f"            {call_expr}")
            lines.append(f"        return Empty()")
        else:
            # has return value
            lines.append(f"        if iscoroutinefunction(driver.{method_name}):")
            lines.append(f"            result = await {call_expr}")
            lines.append(f"        else:")
            lines.append(f"            result = {call_expr}")
            response_expr = _build_servicer_response_expr(
                output_short, messages_by_name, data_model_names, proto_filename, "result",
            )
            lines.append(f"        return {response_expr}")


def _build_servicer_response_expr(
    output_short: str,
    messages_by_name: dict[str, DescriptorProto],
    data_model_names: list[str],
    proto_filename: str,
    var: str,
) -> str:
    """Build the proto response constructor expression for a servicer method."""
    if output_short == "Empty":
        return "Empty()"

    resp_msg = messages_by_name.get(output_short)
    if resp_msg is None:
        return f"{proto_filename}_pb2.{output_short}()"

    if output_short in data_model_names:
        # Data model — construct from model fields
        field_args = ", ".join(f"{f.name}={var}.{f.name}" for f in resp_msg.field)
        return f"{proto_filename}_pb2.{output_short}({field_args})"

    if len(resp_msg.field) == 1 and resp_msg.field[0].name == "value":
        # Single-value wrapper — wrap the raw value
        return f"{proto_filename}_pb2.{output_short}(value={var})"

    # Multi-field response (not detected as data model) — use field-by-field construction
    field_args = ", ".join(f"{f.name}={var}.{f.name}" for f in resp_msg.field)
    return f"{proto_filename}_pb2.{output_short}({field_args})"


def _compile_proto_stubs(
    proto_path: str,
    output_dir: str,
    include_paths: list[str] | None = None,
) -> None:
    """Compile a .proto file to Python pb2/pb2_grpc stubs using grpc_tools.protoc."""
    import re

    import grpc_tools
    from grpc_tools import protoc as grpc_protoc

    proto_path = os.path.abspath(proto_path)
    proto_dir = os.path.dirname(proto_path)
    proto_name = os.path.basename(proto_path)
    output_dir = os.path.abspath(output_dir)

    paths = [proto_dir]
    if include_paths:
        paths.extend(include_paths)

    # grpc_tools bundles well-known types
    wkt_path = os.path.join(os.path.dirname(grpc_tools.__file__), "_proto")
    if os.path.isdir(wkt_path):
        paths.append(wkt_path)

    os.makedirs(output_dir, exist_ok=True)

    args = ["grpc_tools.protoc"]
    for p in paths:
        args.append(f"-I{p}")
    args.extend([
        f"--python_out={output_dir}",
        f"--grpc_python_out={output_dir}",
        proto_name,
    ])

    rc = grpc_protoc.main(args)
    if rc != 0:
        raise click.ClickException(f"protoc compilation failed with exit code {rc}")

    # Fix pb2 imports: make them relative within the package
    # protoc generates absolute imports like:
    #   import power_pb2 as power__pb2  (in _pb2_grpc.py)
    #   from google.protobuf import empty_pb2 as ...  (should stay absolute)
    # We need to convert same-package imports to relative, but leave google.protobuf alone.
    for root, _dirs, files in os.walk(output_dir):
        for fname in files:
            if fname.endswith("_pb2_grpc.py") or fname.endswith("_pb2.py"):
                fpath = os.path.join(root, fname)
                with open(fpath) as f:
                    content = f.read()
                # Fix bare "import X_pb2 as Y" → "from . import X_pb2 as Y"
                content = re.sub(
                    r"^import (\w+_pb2) as ",
                    r"from . import \1 as ",
                    content,
                    flags=re.MULTILINE,
                )
                # Fix "from X import Y_pb2 as Z" where X is NOT google.protobuf
                # → "from . import Y_pb2 as Z"
                content = re.sub(
                    r"^from (?!google\.protobuf)(\S+) import (\w+_pb2) as ",
                    r"from . import \2 as ",
                    content,
                    flags=re.MULTILINE,
                )
                with open(fpath, "w") as f:
                    f.write(content)

    # Create __init__.py in the output directory so Python can import it.
    # Also create in parent dirs, but only if they're inside an existing
    # Python package (stop at the first dir that already has __init__.py).
    init_path = os.path.join(output_dir, "__init__.py")
    if not os.path.exists(init_path):
        with open(init_path, "w") as f:
            f.write("")
    cur = os.path.dirname(output_dir)
    while cur:
        cur_init = os.path.join(cur, "__init__.py")
        if os.path.exists(cur_init):
            break  # reached an existing package root
        # Check if the parent of cur has __init__.py — if not, we've
        # gone outside any Python package, so stop.
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        parent_init = os.path.join(parent, "__init__.py")
        if not os.path.exists(parent_init):
            break  # parent is not a package, don't create __init__.py here
        with open(cur_init, "w") as f:
            f.write("")
        cur = parent


@proto.command("generate")
@click.argument(
    "package_path",
    required=False,
    type=click.Path(exists=True),
)
@click.option(
    "--proto", "-p",
    type=click.Path(exists=True),
    help="Path to a specific .proto file (explicit mode).",
)
@click.option(
    "--output-package",
    help="Python package name (auto-derived from package path if omitted).",
)
@click.option(
    "--output", "-o",
    type=click.Path(),
    help="Output directory (auto-derived from package path if omitted).",
)
@click.option(
    "--proto-path", "-I",
    multiple=True,
    help="Additional include paths for protoc.",
)
@click.option(
    "--servicer-only",
    is_flag=True,
    default=False,
    help="Only generate the servicer adapter (skip interface/client/driver).",
)
@click.option(
    "--language", "-l",
    default="python",
    help="Target language (python, java, typescript, rust). Default: python.",
)
def generate(
    package_path: str | None,
    proto: str | None,
    output_package: str | None,
    output: str | None,
    proto_path: tuple[str, ...],
    servicer_only: bool,
    language: str,
):
    """Generate native gRPC code from .proto interface definitions.

    \b
    Auto-discovery mode (recommended, Python only):
      jmp proto generate packages/jumpstarter-driver-power
      Discovers all .proto files under proto/, derives output paths automatically.

    \b
    Explicit mode (all languages):
      jmp proto generate --proto power.proto --output-package jumpstarter_driver_power -o out/
      jmp proto generate -l java --proto power.proto --output-package dev.jumpstarter.driver.power -o java/jumpstarter-driver-power/

    \b
    Non-Python languages generate a complete driver client package with
    build configuration, proto compilation, and @exportstream port-forward wiring.
    """
    if language != "python" and package_path:
        raise click.ClickException(
            "Auto-discovery mode (PACKAGE_PATH) is only supported for Python.\n"
            "Use --proto with --language for non-Python languages."
        )

    if package_path and proto:
        raise click.ClickException("Specify PACKAGE_PATH or --proto, not both.")

    if language == "python":
        if package_path:
            _generate_from_package(package_path, proto_path, servicer_only)
        elif proto:
            if not output_package:
                raise click.ClickException("--output-package is required in explicit mode.")
            if not output:
                raise click.ClickException("--output is required in explicit mode.")
            _generate_from_proto(proto, output_package, output, proto_path, servicer_only)
        else:
            raise click.ClickException(
                "Provide PACKAGE_PATH for auto-discovery, or --proto for explicit mode.\n"
                "  Example: jmp proto generate packages/jumpstarter-driver-power"
            )
    else:
        # Non-Python: generate driver client package
        if not proto:
            raise click.ClickException("--proto is required for non-Python languages.")
        if not output:
            raise click.ClickException("--output is required for non-Python languages.")
        if not output_package:
            raise click.ClickException("--output-package is required for non-Python languages.")

        _generate_driver_package(language, proto, output_package, output, proto_path)


def _generate_driver_package(
    language: str,
    proto_file: str,
    output_package: str,
    output_dir: str,
    proto_path: tuple[str, ...],
) -> None:
    """Generate a complete driver client package for a non-Python language."""
    from jumpstarter_cli.proto_languages import get_language_generator

    generator = get_language_generator(language)
    if generator is None:
        available = ", ".join(["python", "java", "typescript", "rust"])
        raise click.ClickException(
            f"Unsupported language: {language}. Available: {available}"
        )

    fd = _parse_proto_file(proto_file, list(proto_path) if proto_path else None)
    if not fd.service:
        raise click.ClickException(f"No service found in {proto_file}")

    # Read the .proto source for bundling
    with open(proto_file) as f:
        proto_source = f.read()

    files = generator(fd, output_dir, output_package, proto_source)

    os.makedirs(output_dir, exist_ok=True)
    for rel_path, content in files.items():
        filepath = os.path.join(output_dir, rel_path)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            f.write(content)
        click.echo(f"  Generated {filepath}")

    click.echo(f"\nGenerated {len(files)} file(s) for {language} driver package '{output_package}'.")


def _generate_from_package(
    package_path: str,
    proto_path: tuple[str, ...],
    servicer_only: bool,
) -> None:
    """Auto-discover protos in a driver package and generate code for each."""
    import glob

    package_path = os.path.abspath(package_path)
    proto_dir = os.path.join(package_path, "proto")
    if not os.path.isdir(proto_dir):
        raise click.ClickException(f"No proto/ directory found in {package_path}")

    # Derive Python package name from directory name
    # e.g., "jumpstarter-driver-power" → "jumpstarter_driver_power"
    pkg_dirname = os.path.basename(package_path)
    output_package = pkg_dirname.replace("-", "_")
    output_dir = os.path.join(package_path, output_package)

    # Discover all .proto files under proto/
    proto_files = sorted(glob.glob(os.path.join(proto_dir, "*", "*", "*.proto")))
    if not proto_files:
        raise click.ClickException(f"No .proto files found in {proto_dir}/")

    for proto_file in proto_files:
        click.echo(f"\n--- {os.path.relpath(proto_file, package_path)} ---")
        _generate_from_proto(proto_file, output_package, output_dir, proto_path, servicer_only)

    click.echo(f"\nProcessed {len(proto_files)} proto(s) in {pkg_dirname}")


def _generate_from_proto(
    proto_file: str,
    output_package: str,
    output_dir: str,
    proto_path: tuple[str, ...],
    servicer_only: bool,
) -> None:
    """Generate code from a single .proto file."""
    fd = _parse_proto_file(proto_file, list(proto_path) if proto_path else None)

    if not fd.service:
        raise click.ClickException(f"No service found in {proto_file}")

    # Derive interface name and version from proto package
    parts = fd.package.split(".")
    if len(parts) >= 4 and parts[0] == "jumpstarter" and parts[1] == "interfaces":
        interface_name = parts[2]
        version = parts[3]
    else:
        interface_name = parts[-2] if len(parts) >= 2 else parts[-1]
        version = parts[-1] if len(parts) >= 2 else "v1"

    pb2_output_dir = os.path.join(output_dir, "protocol", interface_name, version)
    os.makedirs(pb2_output_dir, exist_ok=True)

    # Step 1: Compile pb2/pb2_grpc stubs
    _compile_proto_stubs(
        proto_file,
        pb2_output_dir,
        include_paths=list(proto_path) if proto_path else None,
    )
    click.echo(f"Compiled pb2 stubs in {pb2_output_dir}/")

    # Step 2: Generate servicer (lives alongside pb2 stubs)
    servicer_content = _gen_servicer_py(fd, output_package)
    servicer_path = os.path.join(pb2_output_dir, "servicer.py")
    with open(servicer_path, "w") as f:
        f.write(servicer_content)
    click.echo(f"Generated {servicer_path}")

    # Step 3: Optionally generate interface/client/driver in package root
    if not servicer_only:
        os.makedirs(output_dir, exist_ok=True)
        files = {
            "interface.py": _gen_interface_py(fd, output_package),
            "client.py": _gen_client_py(fd, output_package),
            "driver.py": _gen_driver_py(fd, output_package),
            "__init__.py": _gen_init_py(fd, output_package),
        }

        for filename, content in files.items():
            filepath = os.path.join(output_dir, filename)
            with open(filepath, "w") as f:
                f.write(content)
            click.echo(f"Generated {filepath}")
