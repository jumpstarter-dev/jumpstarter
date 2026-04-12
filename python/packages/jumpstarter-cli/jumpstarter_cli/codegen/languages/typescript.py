"""TypeScript language generator for jmp codegen.

Generates:
  1. Per-interface typed clients (e.g., PowerClient.ts)
  2. ExporterClass device wrappers (e.g., DevBoardDevice.ts)
  3. Jest/Vitest test fixtures
  4. package.json metadata
  5. Bundled .proto files for @grpc/proto-loader

All generated code uses `@jumpstarter/client` for session management,
`@grpc/grpc-js` for native gRPC communication, and `@grpc/proto-loader`
for proper protobuf binary serialization.
"""

from __future__ import annotations

import json
import re

from ..engine import LanguageGenerator, register_language
from ..models import (
    CodegenContext,
    DriverInterfaceRef,
    InterfaceMethod,
    MessageField,
    Optionality,
    ProtoEnum,
    ProtoMessage,
)


# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------


def _kebab_to_pascal(name: str) -> str:
    """Convert kebab-case or snake_case to PascalCase."""
    return "".join(part.capitalize() for part in re.split(r"[-_]", name))


def _proto_to_camel(name: str) -> str:
    """Convert PascalCase proto method name to camelCase."""
    if not name:
        return name
    return name[0].lower() + name[1:]


def _scalar_ts_type(proto_type: str) -> str:
    """Map proto scalar type to TypeScript type."""
    mapping = {
        "double": "number",
        "float": "number",
        "int32": "number",
        "int64": "number",
        "uint32": "number",
        "uint64": "number",
        "sint32": "number",
        "sint64": "number",
        "fixed32": "number",
        "fixed64": "number",
        "sfixed32": "number",
        "sfixed64": "number",
        "bool": "boolean",
        "string": "string",
        "bytes": "Uint8Array",
    }
    return mapping.get(proto_type, "unknown")


def _message_ts_name(full_name: str) -> str:
    """Extract short TypeScript type name from fully-qualified proto name."""
    return full_name.rsplit(".", 1)[-1]


def _field_ts_type(field: MessageField) -> str:
    """Determine the TypeScript type for a message field."""
    if field.is_message:
        base = _message_ts_name(field.type_name)
        if field.type_name == "google.protobuf.Empty":
            base = "Record<string, never>"
    elif field.is_enum:
        base = _message_ts_name(field.type_name)
    else:
        base = _scalar_ts_type(field.type_name)

    if field.is_repeated:
        base = f"{base}[]"
    if field.is_optional:
        base = f"{base} | undefined"
    return base


def _is_empty_type(type_name: str) -> bool:
    return type_name == "google.protobuf.Empty"


def _method_return_type(method: InterfaceMethod) -> str:
    if _is_empty_type(method.output_type):
        if method.server_streaming:
            return "AsyncIterable<void>"
        return "void"
    out_type = _message_ts_name(method.output_type)
    if method.server_streaming:
        return f"AsyncIterable<{out_type}>"
    return out_type


def _method_param_type(method: InterfaceMethod) -> str | None:
    if _is_empty_type(method.input_type):
        return None
    return _message_ts_name(method.input_type)


def _client_class_name(interface: DriverInterfaceRef) -> str:
    return interface.service_name.replace("Interface", "Client")


def _resolve_ts_import(interface: DriverInterfaceRef) -> tuple[str, str, bool]:
    """Resolve the TypeScript import for an interface client.

    Returns (import_path, class_name, is_external).
    When is_external is True, the client comes from an external npm package
    and should not be generated.
    """
    hint = interface.drivers.get("typescript")
    if hint and hint.client_class:
        # External package provides the client
        pkg = hint.package or f"@jumpstarter/driver-{interface.name}"
        return pkg, hint.client_class, True

    # Convention: use generated local client
    client_class = _client_class_name(interface)
    return f"./{client_class}", client_class, False


def _proto_filename(interface: DriverInterfaceRef) -> str:
    """Derive the proto filename from the interface.

    jumpstarter.interfaces.power.v1 → power.proto
    """
    parts = interface.proto_package.split(".")
    if len(parts) >= 3 and parts[0] == "jumpstarter" and parts[1] == "interfaces":
        return f"{parts[2]}.proto"
    return f"{parts[-1]}.proto"


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------


def _gen_data_models(interface: DriverInterfaceRef) -> str:
    """Generate TypeScript interfaces for data model messages and enums."""
    lines: list[str] = []

    for enum in interface.enums:
        if enum.doc_comment:
            lines.append(f"/** {enum.doc_comment.strip()} */")
        lines.append(f"export enum {enum.name} {{")
        for value_name, value_number in enum.values:
            lines.append(f"  {value_name} = {value_number},")
        lines.append("}")
        lines.append("")

    for msg in interface.messages:
        if not msg.is_data_model:
            continue
        if msg.doc_comment:
            lines.append(f"/** {msg.doc_comment.strip()} */")
        lines.append(f"export interface {msg.name} {{")
        for field in msg.fields:
            ts_type = _field_ts_type(field)
            field_name = _proto_to_camel(
                "".join(
                    part.capitalize() if i > 0 else part
                    for i, part in enumerate(field.name.split("_"))
                )
            )
            optional = "?" if field.is_optional else ""
            lines.append(f"  readonly {field_name}{optional}: {ts_type};")
        lines.append("}")
        lines.append("")

    return "\n".join(lines)


def _gen_interface_client(
    ctx: CodegenContext,
    interface: DriverInterfaceRef,
) -> str:
    """Generate a per-interface typed client class using @grpc/proto-loader."""
    client_class = _client_class_name(interface)
    proto_file = _proto_filename(interface)
    fq_service = f"{interface.proto_package}.{interface.service_name}"

    # Navigate the proto package to get the service definition from loaded proto
    # e.g. jumpstarter.interfaces.power.v1 → pkg.jumpstarter.interfaces.power.v1
    package_path = "".join(f'["{p}"]' for p in interface.proto_package.split("."))

    lines: list[str] = []

    # File header and imports
    lines.append("// Code generated by jmp codegen. DO NOT EDIT.")
    lines.append("")
    lines.append('import { ExporterSession, createUuidInterceptor } from "@jumpstarter/client";')
    lines.append('import * as grpc from "@grpc/grpc-js";')
    lines.append('import * as protoLoader from "@grpc/proto-loader";')
    lines.append('import * as path from "path";')
    lines.append("")

    # Data model types
    models = _gen_data_models(interface)
    if models.strip():
        lines.append(models)

    # Service-level doc comment
    if interface.doc_comment:
        lines.append("/**")
        for line in interface.doc_comment.strip().split("\n"):
            lines.append(f" * {line.strip()}")
        lines.append(" */")

    lines.append(f"export class {client_class} {{")
    lines.append("  private readonly client: grpc.Client;")
    lines.append("  private readonly interceptor: ReturnType<typeof createUuidInterceptor>;")
    lines.append(f"  private readonly svcDef: any;")
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
    lines.append(f"    this.client = new grpc.Client(session.getAddress(), session.getCredentials(), {{}});")
    lines.append(f"    this.interceptor = createUuidInterceptor(driverUuid);")
    lines.append(f"    const pkg = {client_class}._loadProto();")
    lines.append(f"    const svc = (pkg as any){package_path}.{interface.service_name};")
    lines.append(f"    this.svcDef = svc.service;")
    lines.append(f"  }}")
    lines.append("")

    # Methods
    for method in interface.methods:
        if method.stream_constructor:
            continue

        ts_method = _proto_to_camel(method.name)
        return_type = _method_return_type(method)
        param_type = _method_param_type(method)
        grpc_path = f"/{fq_service}/{method.name}"

        if method.doc_comment:
            lines.append("  /**")
            for line in method.doc_comment.strip().split("\n"):
                lines.append(f"   * {line.strip()}")
            lines.append("   */")

        if method.server_streaming:
            inner_type = _message_ts_name(method.output_type) if not _is_empty_type(method.output_type) else "void"
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

    # Private helper: unary call with proto-loader serialization
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

    # Private helper: server-streaming call with proto-loader serialization
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


def _gen_device_wrapper(ctx: CodegenContext) -> str:
    """Generate the ExporterClass device wrapper class."""
    ec = ctx.exporter_class
    class_name = _kebab_to_pascal(ec.name) + "Device"

    lines: list[str] = []

    lines.append("// Code generated by jmp codegen. DO NOT EDIT.")
    lines.append("")
    lines.append('import { ExporterSession } from "@jumpstarter/client";')

    for iface in ec.interfaces:
        import_path, client_class, _ = _resolve_ts_import(iface)
        lines.append(f'import {{ {client_class} }} from "{import_path}";')

    lines.append("")

    if ec.extends:
        lines.append(f"/** Typed device wrapper for ExporterClass {ec.name} (extends {ec.extends}). */")
    else:
        lines.append(f"/** Typed device wrapper for ExporterClass {ec.name}. */")
    lines.append(f"export class {class_name} {{")

    for iface in ec.interfaces:
        client_class = _client_class_name(iface)
        if iface.optionality == Optionality.OPTIONAL:
            optional = "?"
            comment = "optional — may be undefined"
        else:
            optional = ""
            comment = "required — guaranteed by ExporterClass"
        if iface.doc_comment:
            comment = iface.doc_comment.strip().split("\n")[0]
        lines.append(f"  /** {comment} */")
        lines.append(f"  readonly {iface.name}{optional}: {client_class};")

    lines.append("")
    lines.append(f"  private constructor() {{}}")
    lines.append("")

    lines.append(f"  /**")
    lines.append(f"   * Create a {class_name} from an exporter session.")
    lines.append(f"   * Discovers driver UUIDs via GetReport and creates typed clients.")
    lines.append(f"   */")
    lines.append(f"  static async create(session: ExporterSession): Promise<{class_name}> {{")
    lines.append(f"    const device = new {class_name}();")

    for iface in ec.interfaces:
        client_class = _client_class_name(iface)
        if iface.optionality == Optionality.OPTIONAL:
            lines.append(f'    const {iface.name}Uuid = await session.optionalDriver("{iface.name}");')
            lines.append(f"    if ({iface.name}Uuid) {{")
            lines.append(f"      (device as any).{iface.name} = new {client_class}(session, {iface.name}Uuid);")
            lines.append(f"    }}")
        else:
            lines.append(f'    const {iface.name}Uuid = await session.requireDriver("{iface.name}");')
            lines.append(f"    (device as any).{iface.name} = new {client_class}(session, {iface.name}Uuid);")

    lines.append(f"    return device;")
    lines.append(f"  }}")

    lines.append("}")
    lines.append("")

    return "\n".join(lines)


def _gen_test_fixture(ctx: CodegenContext) -> str:
    """Generate Jest/Vitest test helper."""
    ec = ctx.exporter_class
    wrapper_class = _kebab_to_pascal(ec.name) + "Device"

    lines: list[str] = []
    lines.append("// Code generated by jmp codegen. DO NOT EDIT.")
    lines.append("")
    lines.append('import { ExporterSession } from "@jumpstarter/client";')
    lines.append(f'import {{ {wrapper_class} }} from "./{wrapper_class}";')
    lines.append("")

    lines.append(f"/**")
    lines.append(f" * Create a {wrapper_class} connected to the current jmp shell session.")
    lines.append(f" * Call close() in afterAll to clean up the gRPC connection.")
    lines.append(f" */")
    lines.append(f"export async function create{wrapper_class}(): Promise<{{")
    lines.append(f"  device: {wrapper_class};")
    lines.append(f"  close: () => void;")
    lines.append(f"}}> {{")
    lines.append(f"  const session = ExporterSession.fromEnv();")
    lines.append(f"  const device = await {wrapper_class}.create(session);")
    lines.append(f"  return {{")
    lines.append(f"    device,")
    lines.append(f"    close: () => session.close(),")
    lines.append(f"  }};")
    lines.append(f"}}")
    lines.append("")

    return "\n".join(lines)


def _gen_barrel_index(ctx: CodegenContext) -> str:
    """Generate barrel index.ts re-exporting all generated modules."""
    ec = ctx.exporter_class
    wrapper_class = _kebab_to_pascal(ec.name) + "Device"

    lines: list[str] = []
    lines.append("// Code generated by jmp codegen. DO NOT EDIT.")
    lines.append("")

    for iface in ec.interfaces:
        import_path, client_class, is_external = _resolve_ts_import(iface)
        if is_external:
            # Re-export from the external package
            lines.append(f'export {{ {client_class} }} from "{import_path}";')
        else:
            lines.append(f'export * from "./{client_class}";')

    lines.append(f'export {{ {wrapper_class} }} from "./{wrapper_class}";')

    if ctx.generate_test_fixtures:
        lines.append(f'export {{ create{wrapper_class} }} from "./testing";')

    lines.append("")
    return "\n".join(lines)


def _gen_package_json(ctx: CodegenContext) -> str:
    """Generate package.json for the codegen output."""
    ec = ctx.exporter_class
    pkg_name = ctx.package_name or f"@jumpstarter/device-{ec.name}"

    deps: dict[str, str] = {
        "@jumpstarter/client": "^0.1.0",
        "@grpc/grpc-js": "^1.10.0",
        "@grpc/proto-loader": "^0.7.0",
    }

    # Add external driver package dependencies
    for iface in ec.interfaces:
        hint = iface.drivers.get("typescript")
        if hint and hint.client_class and hint.package:
            version = hint.version or "^0.1.0"
            deps[hint.package] = version

    pkg = {
        "name": pkg_name,
        "version": "0.1.0",
        "description": f"Generated typed device wrapper for Jumpstarter ExporterClass {ec.name}",
        "main": "dist/index.js",
        "types": "dist/index.d.ts",
        "scripts": {
            "build": "tsc",
        },
        "dependencies": deps,
        "devDependencies": {
            "typescript": "^5.4.0",
        },
        "license": "Apache-2.0",
    }
    return json.dumps(pkg, indent=2) + "\n"


def _gen_tsconfig(ctx: CodegenContext) -> str:
    """Generate tsconfig.json for the codegen output."""
    config = {
        "compilerOptions": {
            "target": "ES2022",
            "module": "commonjs",
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
# Language generator
# ---------------------------------------------------------------------------


class TypeScriptLanguageGenerator(LanguageGenerator):
    """TypeScript code generator for jmp codegen.

    Generates per-interface typed clients using `@grpc/grpc-js` with
    `@grpc/proto-loader` for proper protobuf serialization, ExporterClass
    device wrappers, and Jest/Vitest test helpers.
    """

    @property
    def language_name(self) -> str:
        return "typescript"

    def generate_interface_client(
        self,
        ctx: CodegenContext,
        interface: DriverInterfaceRef,
    ) -> dict[str, str]:
        """Generate a per-interface typed client .ts file and bundle the .proto file."""
        # If an external TypeScript client package is declared, skip generation
        _, _, is_external = _resolve_ts_import(interface)
        if is_external:
            return {}

        client_class = _client_class_name(interface)
        source = _gen_interface_client(ctx, interface)
        proto_file = _proto_filename(interface)

        files = {
            f"src/{client_class}.ts": source,
        }

        # Bundle the .proto file for runtime loading by @grpc/proto-loader
        if interface.proto_source:
            files[f"proto/{proto_file}"] = interface.proto_source
        elif interface.proto_file_path:
            try:
                with open(interface.proto_file_path) as f:
                    files[f"proto/{proto_file}"] = f.read()
            except OSError:
                pass

        return files

    def generate_device_wrapper(self, ctx: CodegenContext) -> dict[str, str]:
        ec = ctx.exporter_class
        wrapper_class = _kebab_to_pascal(ec.name) + "Device"

        source = _gen_device_wrapper(ctx)
        index_source = _gen_barrel_index(ctx)

        return {
            f"src/{wrapper_class}.ts": source,
            "src/index.ts": index_source,
        }

    def generate_test_fixture(self, ctx: CodegenContext) -> dict[str, str]:
        source = _gen_test_fixture(ctx)
        return {
            "src/testing.ts": source,
        }

    def generate_package_metadata(self, ctx: CodegenContext) -> dict[str, str]:
        return {
            "package.json": _gen_package_json(ctx),
            "tsconfig.json": _gen_tsconfig(ctx),
        }


# Register with the engine
register_language("typescript", TypeScriptLanguageGenerator)
