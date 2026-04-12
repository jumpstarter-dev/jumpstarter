"""TypeScript driver client package generator for `jmp proto generate --language typescript`.

Generates a complete, publishable npm package from a FileDescriptorProto:
  1. proto/{name}.proto — bundled proto source for @grpc/proto-loader
  2. src/{Service}Client.ts — typed client class with proto-loader + portforward
  3. src/index.ts — barrel export
  4. package.json — npm package metadata
  5. tsconfig.json — TypeScript compiler configuration
"""

from __future__ import annotations

import json
import re

from google.protobuf.descriptor_pb2 import (
    DescriptorProto,
    FieldDescriptorProto,
    FileDescriptorProto,
    MethodDescriptorProto,
)

from . import register_language


# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------


def _proto_to_camel(name: str) -> str:
    """Convert PascalCase proto method name to camelCase."""
    if not name:
        return name
    return name[0].lower() + name[1:]


def _snake_to_camel(name: str) -> str:
    """Convert snake_case field name to camelCase."""
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _scalar_ts_type(proto_type: int) -> str:
    """Map proto scalar type enum to TypeScript type."""
    mapping = {
        FieldDescriptorProto.TYPE_DOUBLE: "number",
        FieldDescriptorProto.TYPE_FLOAT: "number",
        FieldDescriptorProto.TYPE_INT32: "number",
        FieldDescriptorProto.TYPE_INT64: "number",
        FieldDescriptorProto.TYPE_UINT32: "number",
        FieldDescriptorProto.TYPE_UINT64: "number",
        FieldDescriptorProto.TYPE_SINT32: "number",
        FieldDescriptorProto.TYPE_SINT64: "number",
        FieldDescriptorProto.TYPE_FIXED32: "number",
        FieldDescriptorProto.TYPE_FIXED64: "number",
        FieldDescriptorProto.TYPE_SFIXED32: "number",
        FieldDescriptorProto.TYPE_SFIXED64: "number",
        FieldDescriptorProto.TYPE_BOOL: "boolean",
        FieldDescriptorProto.TYPE_STRING: "string",
        FieldDescriptorProto.TYPE_BYTES: "Uint8Array",
    }
    return mapping.get(proto_type, "unknown")


def _resolve_type_name(type_name: str) -> str:
    """Strip leading dot from fully-qualified type names."""
    if type_name.startswith("."):
        return type_name[1:]
    return type_name


def _short_name(full_name: str) -> str:
    """Extract short name from a fully-qualified proto name."""
    return full_name.rsplit(".", 1)[-1]


def _is_empty_type(type_name: str) -> bool:
    resolved = _resolve_type_name(type_name)
    return resolved == "google.protobuf.Empty"


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


def _proto_filename(fd: FileDescriptorProto) -> str:
    """Derive the proto filename from the package.

    jumpstarter.interfaces.power.v1 → power.proto
    """
    parts = fd.package.split(".")
    if len(parts) >= 3 and parts[0] == "jumpstarter" and parts[1] == "interfaces":
        return f"{parts[2]}.proto"
    return f"{parts[-1]}.proto"


# ---------------------------------------------------------------------------
# Field / method type resolution
# ---------------------------------------------------------------------------


def _field_ts_type(field: FieldDescriptorProto, messages_by_name: dict[str, DescriptorProto]) -> str:
    """Determine the TypeScript type for a message field."""
    if field.type == FieldDescriptorProto.TYPE_MESSAGE:
        resolved = _resolve_type_name(field.type_name)
        if resolved == "google.protobuf.Empty":
            base = "Record<string, never>"
        else:
            base = _short_name(resolved)
    elif field.type == FieldDescriptorProto.TYPE_ENUM:
        base = _short_name(_resolve_type_name(field.type_name))
    else:
        base = _scalar_ts_type(field.type)

    if field.label == FieldDescriptorProto.LABEL_REPEATED:
        base = f"{base}[]"
    if field.proto3_optional:
        base = f"{base} | undefined"
    return base


def _method_return_type(method: MethodDescriptorProto) -> str:
    if _is_empty_type(method.output_type):
        if method.server_streaming:
            return "AsyncIterable<void>"
        return "void"
    out_type = _short_name(_resolve_type_name(method.output_type))
    if method.server_streaming:
        return f"AsyncIterable<{out_type}>"
    return out_type


def _method_param_type(method: MethodDescriptorProto) -> str | None:
    if _is_empty_type(method.input_type):
        return None
    return _short_name(_resolve_type_name(method.input_type))


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------


def _gen_data_models(
    fd: FileDescriptorProto,
    data_messages: list[DescriptorProto],
    messages_by_name: dict[str, DescriptorProto],
) -> str:
    """Generate TypeScript interfaces for data model messages and enums."""
    lines: list[str] = []

    # Top-level enums
    for enum_desc in fd.enum_type:
        lines.append(f"export enum {enum_desc.name} {{")
        for v in enum_desc.value:
            lines.append(f"  {v.name} = {v.number},")
        lines.append("}")
        lines.append("")

    # Nested enums from messages
    for msg in fd.message_type:
        for enum_desc in msg.enum_type:
            lines.append(f"export enum {enum_desc.name} {{")
            for v in enum_desc.value:
                lines.append(f"  {v.name} = {v.number},")
            lines.append("}")
            lines.append("")

    # Data model messages as TypeScript interfaces
    for msg in data_messages:
        lines.append(f"export interface {msg.name} {{")
        for field in msg.field:
            ts_type = _field_ts_type(field, messages_by_name)
            field_name = _snake_to_camel(field.name)
            optional = "?" if field.proto3_optional else ""
            lines.append(f"  readonly {field_name}{optional}: {ts_type};")
        lines.append("}")
        lines.append("")

    return "\n".join(lines)


def _gen_client_class(
    fd: FileDescriptorProto,
    data_messages: list[DescriptorProto],
    messages_by_name: dict[str, DescriptorProto],
) -> str:
    """Generate the typed client class for the first service in the proto."""
    service = fd.service[0]
    client_class = service.name.replace("Interface", "Client")
    proto_file = _proto_filename(fd)
    fq_service = f"{fd.package}.{service.name}"

    # Navigate the proto package: jumpstarter.interfaces.power.v1 → ["jumpstarter"]["interfaces"]["power"]["v1"]
    package_path = "".join(f'["{p}"]' for p in fd.package.split("."))

    # Determine which methods are @exportstream (client_streaming = bidi)
    has_portforward = any(m.client_streaming for m in service.method)

    lines: list[str] = []

    # Header
    lines.append("// Code generated by jmp proto generate. DO NOT EDIT.")
    lines.append("")

    # Imports
    imports = ["ExporterSession", "createUuidInterceptor"]
    if has_portforward:
        imports.append("TcpPortforwardAdapter")
    lines.append(f'import {{ {", ".join(imports)} }} from "@jumpstarter/client";')
    lines.append('import * as grpc from "@grpc/grpc-js";')
    lines.append('import * as protoLoader from "@grpc/proto-loader";')
    lines.append('import * as path from "path";')
    lines.append("")

    # Data model types
    models = _gen_data_models(fd, data_messages, messages_by_name)
    if models.strip():
        lines.append(models)

    # Class
    lines.append(f"export class {client_class} {{")
    lines.append("  private readonly client: grpc.Client;")
    lines.append("  private readonly interceptor: ReturnType<typeof createUuidInterceptor>;")
    lines.append("  private readonly svcDef: any;")
    if has_portforward:
        lines.append("  private readonly session: ExporterSession;")
        lines.append("  private readonly driverUuid: string;")
    lines.append("")

    # Static proto loading (cached)
    lines.append(f"  private static _grpcPkg: grpc.GrpcObject | null = null;")
    lines.append("")
    lines.append(f"  private static _loadProto(): grpc.GrpcObject {{")
    lines.append(f"    if (!{client_class}._grpcPkg) {{")
    lines.append(f"      const pd = protoLoader.loadSync(")
    lines.append(f'        path.join(__dirname, "../proto/{proto_file}"),')
    lines.append(f"        {{ keepCase: true, longs: String, enums: String, defaults: true }},")
    lines.append(f"      );")
    lines.append(f"      {client_class}._grpcPkg = grpc.loadPackageDefinition(pd);")
    lines.append(f"    }}")
    lines.append(f"    return {client_class}._grpcPkg;")
    lines.append(f"  }}")
    lines.append("")

    # Constructor
    lines.append(f"  constructor(session: ExporterSession, driverUuid: string) {{")
    if has_portforward:
        lines.append(f"    this.session = session;")
        lines.append(f"    this.driverUuid = driverUuid;")
    lines.append(f"    this.client = new grpc.Client(session.getAddress(), session.getCredentials(), {{}});")
    lines.append(f"    this.interceptor = createUuidInterceptor(driverUuid);")
    lines.append(f"    const pkg = {client_class}._loadProto();")
    lines.append(f"    const svc = (pkg as any){package_path}.{service.name};")
    lines.append(f"    this.svcDef = svc.service;")
    lines.append(f"  }}")
    lines.append("")

    # Methods
    for method in service.method:
        ts_method = _proto_to_camel(method.name)
        grpc_path = f"/{fq_service}/{method.name}"

        if method.client_streaming:
            # @exportstream method — use TcpPortforwardAdapter
            method_arg = _proto_to_camel(method.name)
            lines.append(f"  async {ts_method}(")
            lines.append(f'    method: string = "{method_arg}",')
            lines.append(f"  ): Promise<{{ address: string; port: number }}> {{")
            lines.append(f"    const adapter = await TcpPortforwardAdapter.open(")
            lines.append(f"      this.session,")
            lines.append(f"      this.driverUuid,")
            lines.append(f"      method,")
            lines.append(f"    );")
            lines.append(f"    return {{ address: adapter.address, port: adapter.port }};")
            lines.append(f"  }}")
        elif method.server_streaming:
            # Server-streaming → AsyncIterable<T>
            inner_type = _short_name(_resolve_type_name(method.output_type)) if not _is_empty_type(method.output_type) else "void"
            param_type = _method_param_type(method)
            param_sig = f"request: {param_type}" if param_type else ""
            serialize_arg = "request" if param_type else "{}"

            lines.append(f"  {ts_method}({param_sig}): AsyncIterable<{inner_type}> {{")
            lines.append(f"    const self = this;")
            lines.append(f"    return {{")
            lines.append(f"      [Symbol.asyncIterator]() {{")
            lines.append(f"        return self._serverStream<{inner_type}>(")
            lines.append(f'          "{grpc_path}",')
            lines.append(f'          "{method.name}",')
            lines.append(f"          {serialize_arg},")
            lines.append(f"        );")
            lines.append(f"      }},")
            lines.append(f"    }};")
            lines.append(f"  }}")
        else:
            # Unary
            return_type = _method_return_type(method)
            param_type = _method_param_type(method)
            param_sig = f"request: {param_type}" if param_type else ""
            serialize_arg = "request" if param_type else "{}"

            lines.append(f"  async {ts_method}({param_sig}): Promise<{return_type}> {{")
            lines.append(f"    return this._unary(")
            lines.append(f'      "{grpc_path}",')
            lines.append(f'      "{method.name}",')
            lines.append(f"      {serialize_arg},")
            lines.append(f"    );")
            lines.append(f"  }}")

        lines.append("")

    # Close method
    lines.append("  /** Close the gRPC client. */")
    lines.append("  close(): void {")
    lines.append("    this.client.close();")
    lines.append("  }")
    lines.append("")

    # Private helper: unary
    lines.append("  private _unary<T>(path: string, methodName: string, request: any): Promise<T> {")
    lines.append("    const methodDef = this.svcDef[methodName];")
    lines.append("    return new Promise<T>((resolve, reject) => {")
    lines.append("      this.client.makeUnaryRequest(")
    lines.append("        path,")
    lines.append("        methodDef.requestSerialize,")
    lines.append("        methodDef.responseDeserialize,")
    lines.append("        request,")
    lines.append("        new grpc.Metadata(),")
    lines.append("        { interceptors: [this.interceptor] },")
    lines.append("        (err: grpc.ServiceError | null, resp?: T) => {")
    lines.append("          if (err) reject(err);")
    lines.append("          else resolve(resp as T);")
    lines.append("        },")
    lines.append("      );")
    lines.append("    });")
    lines.append("  }")
    lines.append("")

    # Private helper: server-streaming
    lines.append("  private async *_serverStream<T>(path: string, methodName: string, request: any): AsyncIterableIterator<T> {")
    lines.append("    const methodDef = this.svcDef[methodName];")
    lines.append("    const stream = this.client.makeServerStreamRequest(")
    lines.append("      path,")
    lines.append("      methodDef.requestSerialize,")
    lines.append("      methodDef.responseDeserialize,")
    lines.append("      request,")
    lines.append("      new grpc.Metadata(),")
    lines.append("      { interceptors: [this.interceptor] },")
    lines.append("    );")
    lines.append("    for await (const msg of stream) {")
    lines.append("      yield msg;")
    lines.append("    }")
    lines.append("  }")

    lines.append("}")
    lines.append("")

    return "\n".join(lines)


def _gen_barrel_index(client_class: str, data_model_names: list[str]) -> str:
    """Generate barrel src/index.ts re-exporting the client and data models."""
    lines: list[str] = []
    lines.append("// Code generated by jmp proto generate. DO NOT EDIT.")
    lines.append("")
    lines.append(f'export * from "./{client_class}";')
    lines.append("")
    return "\n".join(lines)


def _gen_package_json(output_package: str) -> str:
    pkg = {
        "name": output_package,
        "version": "0.1.0",
        "main": "dist/index.js",
        "types": "dist/index.d.ts",
        "scripts": {
            "build": "tsc",
        },
        "dependencies": {
            "@jumpstarter/client": "^0.1.0",
            "@grpc/grpc-js": "^1.10.0",
            "@grpc/proto-loader": "^0.7.0",
        },
        "devDependencies": {
            "typescript": "^5.4.0",
        },
        "license": "Apache-2.0",
    }
    return json.dumps(pkg, indent=2) + "\n"


def _gen_tsconfig() -> str:
    config = {
        "compilerOptions": {
            "target": "ES2022",
            "module": "node16",
            "moduleResolution": "node16",
            "lib": ["ES2022"],
            "declaration": True,
            "strict": True,
            "esModuleInterop": True,
            "skipLibCheck": True,
            "forceConsistentCasingInFileNames": True,
            "outDir": "dist",
            "rootDir": "src",
        },
        "include": ["src/**/*.ts"],
    }
    return json.dumps(config, indent=2) + "\n"


# ---------------------------------------------------------------------------
# Main generator function
# ---------------------------------------------------------------------------


def generate_typescript_driver_package(
    fd: FileDescriptorProto,
    output_dir: str,
    output_package: str,
    proto_source: str,
) -> dict[str, str]:
    """Generate a complete TypeScript driver client package from a FileDescriptorProto.

    Args:
        fd: Parsed proto file descriptor.
        output_dir: Target directory for generated files.
        output_package: npm package name (e.g. "@jumpstarter/driver-power").
        proto_source: Raw .proto file content to bundle.

    Returns:
        Mapping of relative_path → file_content.
    """
    if not fd.service:
        return {}

    service = fd.service[0]
    client_class = service.name.replace("Interface", "Client")
    proto_file = _proto_filename(fd)

    # Collect data models
    messages_by_name: dict[str, DescriptorProto] = {m.name: m for m in fd.message_type}
    rpc_types: set[str] = set()
    for method in service.method:
        for type_name in (method.input_type, method.output_type):
            rpc_types.add(type_name.rsplit(".", 1)[-1])
    data_messages = [m for m in fd.message_type if _is_data_message(m, rpc_types, list(service.method))]
    data_model_names = [m.name for m in data_messages]

    files: dict[str, str] = {}

    # 1. Bundle the .proto source
    files[f"proto/{proto_file}"] = proto_source

    # 2. Client class
    files[f"src/{client_class}.ts"] = _gen_client_class(fd, data_messages, messages_by_name)

    # 3. Barrel index
    files["src/index.ts"] = _gen_barrel_index(client_class, data_model_names)

    # 4. package.json
    files["package.json"] = _gen_package_json(output_package)

    # 5. tsconfig.json
    files["tsconfig.json"] = _gen_tsconfig()

    return files


register_language("typescript", generate_typescript_driver_package)
