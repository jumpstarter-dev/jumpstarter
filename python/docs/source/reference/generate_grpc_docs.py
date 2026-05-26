#!/usr/bin/env python3
"""Generate markdown API reference from gRPC protocol .proto files."""

import glob
import os
import re
import sys
from typing import TypedDict


class Field(TypedDict):
    name: str
    number: int
    type: str
    label: str
    description: str


class RpcMethod(TypedDict):
    name: str
    input_type: str
    output_type: str
    client_streaming: bool
    server_streaming: bool
    description: str


class Service(TypedDict):
    name: str
    description: str
    methods: list[RpcMethod]


class Message(TypedDict):
    name: str
    description: str
    fields: list[Field]


class EnumValue(TypedDict):
    name: str
    number: int
    description: str


class EnumDef(TypedDict):
    name: str
    description: str
    values: list[EnumValue]


class ProtoFile(TypedDict):
    filename: str
    package: str
    syntax: str
    services: list[Service]
    messages: list[Message]
    enums: list[EnumDef]

PROTO_DIRS = [
    os.path.join(
        os.path.dirname(__file__),
        "../../../../protocol/proto/jumpstarter/v1",
    ),
    os.path.join(
        os.path.dirname(__file__),
        "../../../../protocol/proto/jumpstarter/client/v1",
    ),
]
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "grpc")

_SKIP_PREFIXES = ("//", "enum ", "message ", "oneof ", "option (", "option;", "reserved ")


def _extract_leading_comment(lines: list[str], end_index: int) -> str:
    comment_lines = []
    i = end_index - 1
    while i >= 0:
        stripped = lines[i].strip()
        if stripped.startswith("//"):
            comment_text = stripped.lstrip("/").strip()
            comment_lines.insert(0, comment_text)
            i -= 1
        else:
            break
    return " ".join(comment_lines).strip()


def _extract_inline_comment(line: str) -> str:
    match = re.search(r"//\s*(.*)", line.split(";")[1] if ";" in line else "")
    if match:
        return match.group(1).strip()
    parts = line.split("//", 1)
    if len(parts) > 1:
        comment_text = parts[1].strip()
        return comment_text
    return ""


def _extract_field_description(body_lines: list[str], i: int) -> str:
    desc = _extract_inline_comment(body_lines[i])
    if not desc:
        desc = _extract_leading_comment(body_lines, i)
    return desc


def _strip_strings_and_comments(line: str) -> str:
    result = re.sub(r"//.*$", "", line)
    result = re.sub(r'"[^"]*"', "", result)
    return result


def _find_brace_end(lines: list[str], decl_line_idx: int) -> int:
    decl_line = _strip_strings_and_comments(lines[decl_line_idx])
    brace_depth = 0
    for ch in decl_line:
        if ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
    if brace_depth == 0:
        return decl_line_idx + 1
    j = decl_line_idx + 1
    while j < len(lines) and brace_depth > 0:
        stripped = _strip_strings_and_comments(lines[j])
        for ch in stripped:
            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
        j += 1
    return j


def _is_field_line(line: str) -> bool:
    return not any(line.startswith(p) for p in _SKIP_PREFIXES) and "}" not in line.split("=")[0]


def _parse_nested_message(
    body_lines: list[str], i: int, parent_name: str
) -> tuple[int, list[Message]]:
    msg_match = re.match(r"message\s+(\w+)\s*\{", body_lines[i].strip())
    if not msg_match:
        return i + 1, []
    nested_name = msg_match.group(1)
    full_name = f"{parent_name}.{nested_name}" if parent_name else nested_name
    nested_desc = _extract_leading_comment(body_lines, i)
    j = _find_brace_end(body_lines, i)
    nested_body = body_lines[i + 1 : j - 1]
    nested_fields, sub_nested = _parse_fields(nested_body, full_name)
    result: list[Message] = [Message(name=full_name, description=nested_desc, fields=nested_fields)]
    result.extend(sub_nested)
    return j, result


def _parse_oneof_block(
    body_lines: list[str], i: int
) -> tuple[int, list[Field]]:
    oneof_match = re.match(r"oneof\s+(\w+)\s*\{", body_lines[i].strip())
    if not oneof_match:
        return i + 1, []
    oneof_name = oneof_match.group(1)
    fields: list[Field] = []
    i += 1
    while i < len(body_lines):
        oneof_line = body_lines[i].strip()
        if oneof_line == "}":
            i += 1
            break
        field_match = re.match(r"(\w+)\s+(\w+)\s*=\s*(\d+)\s*;", oneof_line)
        if field_match:
            fields.append(Field(
                name=field_match.group(2),
                number=int(field_match.group(3)),
                type=field_match.group(1),
                label=f"oneof {oneof_name}",
                description=_extract_field_description(body_lines, i),
            ))
        i += 1
    return i, fields


def _parse_fields(
    body_lines: list[str], parent_name: str = ""
) -> tuple[list[Field], list[Message]]:
    fields: list[Field] = []
    nested_messages: list[Message] = []
    i = 0
    while i < len(body_lines):
        line = body_lines[i].strip()

        if re.match(r"message\s+(\w+)\s*\{", line):
            i, nested = _parse_nested_message(body_lines, i, parent_name)
            nested_messages.extend(nested)
            continue

        if re.match(r"oneof\s+(\w+)\s*\{", line):
            i, oneof_fields = _parse_oneof_block(body_lines, i)
            fields.extend(oneof_fields)
            continue

        map_match = re.match(r"(map<\w+,\s*\w+>)\s+(\w+)\s*=\s*(\d+)\s*;", line)
        if map_match:
            fields.append(Field(
                name=map_match.group(2),
                number=int(map_match.group(3)),
                type=map_match.group(1),
                label="",
                description=_extract_field_description(body_lines, i),
            ))
            i += 1
            continue

        field_match = re.match(
            r"(optional\s+|repeated\s+)?(\S+)\s+(\w+)\s*=\s*(\d+)\s*", line
        )
        if field_match and _is_field_line(line):
            label_raw = (field_match.group(1) or "").strip()
            fields.append(Field(
                name=field_match.group(3),
                number=int(field_match.group(4)),
                type=field_match.group(2),
                label=label_raw,
                description=_extract_field_description(body_lines, i),
            ))

        i += 1

    return fields, nested_messages


def parse_proto_file(filepath: str) -> ProtoFile:
    with open(filepath, encoding="utf-8") as f:
        content = f.read()
    lines = content.split("\n")

    result = ProtoFile(
        filename=os.path.basename(filepath),
        package="",
        syntax="",
        services=[],
        messages=[],
        enums=[],
    )

    pkg_match = re.search(r"^package\s+([\w.]+)\s*;", content, re.MULTILINE)
    if pkg_match:
        result["package"] = pkg_match.group(1)

    syntax_match = re.search(r'^syntax\s*=\s*"(\w+)"\s*;', content, re.MULTILINE)
    if syntax_match:
        result["syntax"] = syntax_match.group(1)

    _parse_top_level(lines, result)
    return result


def _parse_service_block(lines: list[str], i: int) -> tuple[int, Service]:
    service_match = re.match(r"service\s+(\w+)\s*\{", lines[i].strip())
    if not service_match:
        raise ValueError(f"Expected service declaration at line {i}: {lines[i]}")
    description = _extract_leading_comment(lines, i)
    j = _find_brace_end(lines, i)
    body_lines = lines[i + 1 : j - 1]
    methods = _parse_rpc_methods(body_lines)
    return j, Service(
        name=service_match.group(1),
        description=description,
        methods=methods,
    )


def _parse_message_block(lines: list[str], i: int) -> tuple[int, Message, list[Message]]:
    msg_match = re.match(r"message\s+(\w+)\s*\{", lines[i].strip())
    if not msg_match:
        raise ValueError(f"Expected message declaration at line {i}: {lines[i]}")
    msg_name = msg_match.group(1)
    description = _extract_leading_comment(lines, i)
    j = _find_brace_end(lines, i)
    body_lines = lines[i + 1 : j - 1]
    fields, nested = _parse_fields(body_lines, msg_name)
    return j, Message(name=msg_name, description=description, fields=fields), nested


def _parse_enum_block(lines: list[str], i: int) -> tuple[int, EnumDef]:
    enum_match = re.match(r"enum\s+(\w+)\s*\{", lines[i].strip())
    if not enum_match:
        raise ValueError(f"Expected enum declaration at line {i}: {lines[i]}")
    description = _extract_leading_comment(lines, i)
    j = _find_brace_end(lines, i)
    body_lines = lines[i + 1 : j - 1]
    values = _parse_enum_values(body_lines)
    return j, EnumDef(
        name=enum_match.group(1),
        description=description,
        values=values,
    )


def _parse_top_level(lines: list[str], result: ProtoFile) -> None:
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if re.match(r"service\s+(\w+)\s*\{", line):
            i, service = _parse_service_block(lines, i)
            result["services"].append(service)
            continue

        if re.match(r"message\s+(\w+)\s*\{", line):
            i, msg, nested = _parse_message_block(lines, i)
            result["messages"].append(msg)
            result["messages"].extend(nested)
            continue

        if re.match(r"enum\s+(\w+)\s*\{", line):
            i, enum_data = _parse_enum_block(lines, i)
            result["enums"].append(enum_data)
            continue

        i += 1


def _parse_rpc_methods(body_lines: list[str]) -> list[RpcMethod]:
    methods: list[RpcMethod] = []
    for i, line in enumerate(body_lines):
        stripped = line.strip()
        rpc_match = re.match(
            r"rpc\s+(\w+)\s*\(\s*(stream\s+)?(\S+)\s*\)\s*returns\s*\(\s*(stream\s+)?(\S+)\s*\)",
            stripped,
        )
        if rpc_match:
            methods.append(RpcMethod(
                name=rpc_match.group(1),
                input_type=rpc_match.group(3),
                output_type=rpc_match.group(5),
                client_streaming=rpc_match.group(2) is not None,
                server_streaming=rpc_match.group(4) is not None,
                description=_extract_leading_comment(body_lines, i),
            ))
    return methods


def _parse_enum_values(body_lines: list[str]) -> list[EnumValue]:
    values: list[EnumValue] = []
    for i, line in enumerate(body_lines):
        stripped = line.strip()
        value_match = re.match(r"(\w+)\s*=\s*(0x[\da-fA-F]+|\d+)\s*;", stripped)
        if value_match:
            values.append(EnumValue(
                name=value_match.group(1),
                number=int(value_match.group(2), 0),
                description=_extract_field_description(body_lines, i),
            ))
    return values


def render_service(service: Service) -> str:
    sections = []
    sections.append(f"### {service['name']}\n")
    if service["description"]:
        sections.append(f"{service['description']}\n")
    if service["methods"]:
        sections.append("| Method | Request | Response | Description |")
        sections.append("| --- | --- | --- | --- |")
        for method in service["methods"]:
            streaming_prefix_in = "stream " if method["client_streaming"] else ""
            streaming_prefix_out = "stream " if method["server_streaming"] else ""
            request = f"{streaming_prefix_in}`{method['input_type']}`"
            response = f"{streaming_prefix_out}`{method['output_type']}`"
            desc = method["description"].replace("|", r"\|")
            sections.append(
                f"| `{method['name']}` | {request} | {response} | {desc} |"
            )
    return "\n".join(sections) + "\n"


def render_message(message: Message) -> str:
    sections = []
    display_name = message["name"]
    sections.append(f"### {display_name}\n")
    if message["description"]:
        sections.append(f"{message['description']}\n")
    if message["fields"]:
        sections.append("| Field | Number | Type | Label | Description |")
        sections.append("| --- | --- | --- | --- | --- |")
        for field in message["fields"]:
            desc = field["description"].replace("|", r"\|")
            sections.append(
                f"| `{field['name']}` | {field['number']} | `{field['type']}`"
                f" | {field['label']} | {desc} |"
            )
    else:
        sections.append("*No fields defined.*\n")
    return "\n".join(sections) + "\n"


def render_enum(enum_def: EnumDef) -> str:
    sections = []
    sections.append(f"### {enum_def['name']}\n")
    if enum_def["description"]:
        sections.append(f"{enum_def['description']}\n")
    if enum_def["values"]:
        sections.append("| Name | Number | Description |")
        sections.append("| --- | --- | --- |")
        for value in enum_def["values"]:
            desc = value["description"].replace("|", r"\|")
            sections.append(f"| `{value['name']}` | {value['number']} | {desc} |")
    return "\n".join(sections) + "\n"


def render_proto_doc(proto_data: ProtoFile) -> str:
    name = proto_data["filename"].replace(".proto", "")
    sections = []
    sections.append(f"# {name}\n")
    sections.append(f"`{proto_data['package']}`\n")

    if proto_data["services"]:
        sections.append("## Services\n")
        for service in proto_data["services"]:
            sections.append(render_service(service))

    if proto_data["messages"]:
        sections.append("## Messages\n")
        for message in proto_data["messages"]:
            sections.append(render_message(message))

    if proto_data["enums"]:
        sections.append("## Enums\n")
        for enum_def in proto_data["enums"]:
            sections.append(render_enum(enum_def))

    return "\n".join(sections)


def main(
    proto_dirs: list[str] | None = None,
    output_dir: str = OUTPUT_DIR,
) -> None:
    if proto_dirs is None:
        proto_dirs = PROTO_DIRS

    all_protos: list[str] = []
    for proto_dir in proto_dirs:
        found = sorted(glob.glob(os.path.join(proto_dir, "*.proto")))
        all_protos.extend(found)

    if not all_protos:
        print(f"No proto files found in {proto_dirs}")
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    generated = []
    for proto_file in all_protos:
        print(f"Processing {os.path.basename(proto_file)}")
        proto_data = parse_proto_file(proto_file)
        content = render_proto_doc(proto_data)
        slug = proto_data["filename"].replace(".proto", "")
        filename = f"{slug}.md"
        with open(os.path.join(output_dir, filename), "w", encoding="utf-8") as f:
            f.write(content)
        generated.append((slug, filename))

    index_lines = [
        "# gRPC Protocol\n",
        "This section provides reference documentation for the Jumpstarter gRPC",
        "protocol definitions. The documentation is autogenerated from the `.proto`",
        "source files.\n",
    ]
    for slug, filename in generated:
        index_lines.append(f"- [{slug}]({filename})")
    index_lines.append("")
    index_lines.append("```{toctree}")
    index_lines.append(":maxdepth: 1")
    index_lines.append(":hidden:")
    index_lines.append("")
    for _, filename in generated:
        index_lines.append(filename)
    index_lines.append("```")

    with open(os.path.join(output_dir, "index.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(index_lines) + "\n")

    print(f"Generated {len(generated)} gRPC docs in {output_dir}/")


if __name__ == "__main__":
    main()
