"""
Render a google.protobuf.FileDescriptorProto as human-readable .proto source.

Takes the FileDescriptorProto produced by descriptor_builder.build_file_descriptor
and emits the equivalent .proto text (service first, then messages), reattaching
doc comments captured in source_code_info as leading // comments.
"""

from __future__ import annotations

from google.protobuf.descriptor_pb2 import (
    DescriptorProto,
    EnumDescriptorProto,
    FieldDescriptorProto,
    FileDescriptorProto,
    MethodDescriptorProto,
)

# FileDescriptorProto field numbers (used to look up source_code_info comments)
_FDP_MESSAGE_TYPE = 4
_FDP_SERVICE = 6
_SDP_METHOD = 2
_DP_FIELD = 2


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
    field: FieldDescriptorProto,
    package: str,
    indent: str,
    fd: FileDescriptorProto | None = None,
    field_path: list[int] | None = None,
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
    msg: DescriptorProto,
    package: str,
    indent: str,
    fd: FileDescriptorProto | None = None,
    msg_path: list[int] | None = None,
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
    method: MethodDescriptorProto,
    package: str,
    indent: str,
    fd: FileDescriptorProto | None = None,
    method_path: list[int] | None = None,
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

    # Services (proto convention: service first, then messages)
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

    # File-level enums (hoisted model/Literal enums referenced as `.{package}.{Name}`)
    for enum_desc in fd.enum_type:
        lines.append("")
        lines.append(_render_enum(enum_desc, ""))

    lines.append("")
    return "\n".join(lines)
