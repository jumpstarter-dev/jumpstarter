# JEP-0011: Protobuf Introspection and Interface Generation

| Field          | Value                                                              |
| -------------- | ------------------------------------------------------------------ |
| **JEP**        | 0011                                                               |
| **Title**      | Protobuf Introspection and Interface Generation                    |
| **Author(s)**  | @kirkbrauer (Kirk Brauer)                                          |
| **Status**     | Accepted                                                           |
| **Type**       | Standards Track                                                    |
| **Created**    | 2026-04-06                                                         |
| **Updated**    | 2026-05-09                                                         |
| **Discussion** | [PR #565](https://github.com/jumpstarter-dev/jumpstarter/pull/565) |

---

## Abstract

This JEP makes Jumpstarter driver interfaces discoverable to non-Python clients by introducing `.proto` files as the canonical schema artifact for each driver interface. A new **codegen CLI** introspects Python interface classes at development time and emits `.proto` source files that are committed to each driver package. A companion **interface check CLI** runs in CI to detect drift between Python interfaces and their committed `.proto` files. The existing gRPC Server Reflection service and the `DriverInstanceReport.file_descriptor_proto` field serve the same compiled descriptor set at runtime so that tools like `grpcurl`, Buf, and polyglot codegen can discover the driver API without reading Python source.

This JEP keeps the Jumpstarter wire protocol unchanged — `DriverCall` remains the transport. The `.proto` schemas serve as an advisory description layer that enables polyglot discovery and future native-gRPC migration. Proto-first workflows (defining interfaces as `.proto` files and generating Python scaffolding) are deferred to a follow-up JEP focused on non-Python codegen.

## Motivation

Today, the `DriverInstanceReport` returned by `GetReport` contains driver UUIDs, labels, parent-child relationships, and human-readable `methods_description` text. It does not include machine-readable method signatures — parameter names, types, return types, or call semantics (unary vs. streaming). This means non-Python clients cannot discover the shape of a driver's API without out-of-band knowledge, limiting Jumpstarter to a single-language ecosystem.

The `@export` decorator already has access to the full method signature via `inspect.signature()`, and the interface classes already carry type annotations. However, none of this information is surfaced in a structured, interoperable format. A JVM-based test runner, a TypeScript MCP server, or a Rust flash utility all have to reverse-engineer method names, argument types, and streaming semantics from Python source code or informal documentation.

Additionally, teams that want to define interface contracts upfront — before writing any driver implementation — currently have no supported workflow. A proto-first path would let architects define the interface as a `.proto` file and generate the Python scaffolding from it, following the standard gRPC development pattern while remaining fully compatible with Jumpstarter's existing driver model.

This JEP addresses three concrete gaps:

1. **Runtime introspection** — non-Python clients have no way to discover driver APIs programmatically.
2. **Schema portability** — there is no language-neutral description of Jumpstarter driver interfaces that standard protobuf/gRPC tooling can consume.
3. **Schema stability** — there is no committed, reviewable artifact describing a driver interface. Changes to Python signatures silently change the wire contract, with no diff for reviewers and no CI signal for polyglot consumers.

### User Stories

- **As a** Python driver developer, **I want** an opt-in linter that flags `@export` methods missing type annotations, **so that** interfaces I choose to expose to polyglot consumers are fully typed before the `.proto` file is generated.

- **As a** Java test engineer writing Android device tests, **I want to** discover all available methods on a leased device's power driver — including parameter types, return types, and streaming semantics — **so that** I can generate type-safe Kotlin stubs instead of hand-writing `DriverCall` invocations with magic string method names.

- **As a** tools developer building a device management dashboard, **I want to** point standard gRPC tooling (`grpcurl`, Postman, Buf Studio) at an exporter and discover every available driver interface with full type information, **so that** I can prototype interactions without reading Python source code.

- **As a** CI pipeline author, **I want to** run a compatibility check in CI that verifies the Python driver interface hasn't drifted from the committed `.proto` definition, **so that** cross-language clients don't silently break when a driver evolves.

## Proposal

### Overview

This proposal adds three capabilities to Jumpstarter, all centered on committed `.proto` files as the canonical schema artifact:

1. **Codegen CLI (Python → `.proto`)** — a developer-invoked command that introspects a `DriverInterface` class and emits a `.proto` source file. The `.proto` file is committed alongside the driver package that defines the interface.
2. **Interface check CLI (drift detection)** — runs in CI to verify the committed `.proto` file still matches the Python interface. Reports any method, parameter, return-type, or streaming-semantics mismatch as a test failure.
3. **Runtime descriptor exposure** — the exporter loads the pre-compiled descriptor set (produced by `protoc --descriptor_set_out` from the committed `.proto` files), registers the services with gRPC Server Reflection, and embeds the raw bytes in `DriverInstanceReport.file_descriptor_proto`.

The `.proto` files are the source of truth. Introspection happens once, at development time, when the author runs the codegen CLI; it does **not** happen at exporter startup or at Python import time. This mirrors the standard gRPC development workflow and keeps the exporter's runtime free of schema-construction work.

**CLI naming is intentionally deferred.** This JEP does not commit to a concrete command surface for the codegen and check tools. Whether they ship as `jmp` subcommands, a separate `jmp-devel` binary, standalone executables, or some other shape is a UX decision better made during implementation, when we can weigh how much of the developer toolchain ends up under one umbrella. Throughout this document, "the codegen CLI" and "the interface check CLI" are used as descriptive names; bash code blocks use `<codegen>` and `<interface-check>` as placeholders for whatever the final invocation turns out to be.

Proto-first workflows — authoring `.proto` files and generating Python interface/client/driver scaffolding from them — are **out of scope for this JEP**. They are planned as a follow-up JEP once non-Python codegen is ready to consume the committed `.proto` files.

### Wire Protocol: `DriverCall` Remains Unchanged

An important design constraint: **this JEP does not change the wire protocol.** The existing `DriverCall` and `StreamingDriverCall` RPCs — where the client sends a method name as a string and arguments as `google.protobuf.Value` — remain the actual transport mechanism. The auto-generated client code still calls `self.call("on")` and `self.streamingcall("read")` under the hood. The auto-generated driver adapter still receives dispatch through the existing `@export` decorator and `Driver` base class machinery.

The `.proto` files and `FileDescriptorProto` descriptors serve as a **description layer** on top of the existing dispatch mechanism — they describe what methods exist, what types they use, and what streaming semantics they have. They do not replace `DriverCall` with actual protobuf-native gRPC service implementations (where `PowerInterface` would be a real gRPC service with compiled request/response message stubs). That migration would be a significant breaking change to the exporter protocol, affecting every existing client and driver, and is explicitly out of scope for this JEP.

In concrete terms:

- **What the proto IS used for:** introspection (`GetReport`, gRPC reflection), compatibility checking (the interface check CLI, `buf breaking`), documentation, and polyglot codegen.
- **What the proto is NOT used for:** actual RPC transport. The `DriverCall(uuid="...", method="on", args=[])` message continues to be the wire format.

A future JEP will propose migrating to native protobuf service implementations — where `protoc`-generated stubs handle serialization directly and `DriverCall` is retired — but that is a separate, breaking change with its own migration path. A design sketch for this future work is included at the end of this JEP for context.

#### gRPC reflection is advisory in this JEP

gRPC reflection will advertise services described by the committed `.proto` files — for example, `jumpstarter.interfaces.power.v1.PowerInterface.On(Empty)`. Because the wire protocol is unchanged, **those services are not backed by native gRPC handlers in this JEP**. A client that discovers the service through reflection and attempts to invoke it directly (e.g., `grpcurl -d '{}' host:port jumpstarter.interfaces.power.v1.PowerInterface/On`) will receive `UNIMPLEMENTED`.

Reflection here is deliberately **advisory** — it exposes the schema so polyglot clients, codegen pipelines, and documentation tooling can discover the driver API and generate typed stubs that drive the existing `DriverCall` transport. The follow-up native-gRPC JEP will add handlers so reflected services become directly invocable without changing the proto schema produced by this JEP.

### `FileDescriptorProto` as the Schema Format

Rather than defining a custom schema message, this proposal uses protobuf's own self-description mechanism: `google.protobuf.FileDescriptorProto`. This is the same format that gRPC Server Reflection serves, that `buf` understands natively, and that every language's protobuf library can parse.

A `FileDescriptorProto` fully describes a `.proto` file in binary form: its package name, message definitions (with field names, types, and numbers), service definitions (with method names, request/response types, and streaming semantics), and import dependencies. This is strictly more expressive than any custom schema format.

Using it means there is one descriptor format throughout the entire system — generation, runtime introspection, registry, and codegen all consume the same artifact.

### Build-time introspection of `@export` methods

Introspection runs at codegen CLI invocation time, not at import or exporter startup. The `@export` decorator itself is unchanged — it still stamps markers on the function for `DriverCall` dispatch. Type information is read directly from the live class via `inspect.signature()` when the CLI tool loads the interface module:

```python
# inside the codegen CLI
sig = inspect.signature(method)
call_type = _infer_call_type(method)   # UNARY | SERVER_STREAMING | BIDI_STREAMING
params = [
    (p.name, p.annotation, p.default)
    for p in sig.parameters.values()
    if p.name != "self"
]
return_type = sig.return_annotation
```

The `_infer_call_type()` helper examines both the parameter and return annotations to determine streaming semantics: `AsyncGenerator[T]` or `Generator[T]` as a return type indicates server streaming, an `AsyncGenerator` parameter indicates client streaming, and the combination indicates bidirectional streaming (as used by the TCP driver). All other signatures indicate unary calls. Methods decorated with `@exportstream` (detected via the `MARKER_STREAMCALL` attribute) are handled separately — they are raw byte stream constructors that use a `StreamData { bytes payload }` message for native gRPC bidi streaming (see "Driver Patterns and Introspection Scope" in Design Details).

Because introspection is build-time only, there is no per-method metadata stored on function objects, no import-time overhead, and no runtime coupling between the dispatch layer and schema description.

### Type Mapping

The following table defines how Python type annotations map to protobuf field types:

| Python type                                        | Proto type                                         | Notes                                                                                  |
| -------------------------------------------------- | -------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `str`                                              | `TYPE_STRING`                                      |                                                                                        |
| `int`                                              | `TYPE_INT64`                                       |                                                                                        |
| `float`                                            | `TYPE_DOUBLE`                                      |                                                                                        |
| `bool`                                             | `TYPE_BOOL`                                        |                                                                                        |
| `bytes`                                            | `TYPE_BYTES`                                       |                                                                                        |
| `UUID`                                             | `TYPE_STRING`                                      | Serializes as string through `encode_value`                                            |
| `None` / no return                                 | `google.protobuf.Empty`                            | Uses well-known type                                                                   |
| `dict` / `Any`                                     | `google.protobuf.Value`                            | Dynamic/untyped fallback                                                               |
| Pydantic `BaseModel`                               | Generated `DescriptorProto`                        | Fields introspected via `model_fields`; the primary data model pattern in the codebase |
| `@dataclass`                                       | Generated `DescriptorProto`                        | Fields introspected via `dataclasses.fields()`                                         |
| `list[T]` / `set[T]`                               | `repeated T`                                       | Common: `list[int]`, `list[DidValue]` in UDS drivers                                   |
| `enum.Enum` / `StrEnum`                            | Proto `enum`                                       | e.g., `UdsSessionType`, `Compression`, `Mode`                                          |
| `Literal["a", "b"]`                                | Proto `enum`                                       | String literals mapped to generated enum values                                        |
| `AsyncGenerator[T]` / `Generator[T]`               | `server_streaming: true`                           | Method marked as server streaming                                                      |
| Bidirectional (generator param + generator return) | `client_streaming: true`, `server_streaming: true` | Used by TCP driver, mapped to bidi stream                                              |
| `@exportstream` context manager                    | Bidi stream `StreamData { bytes payload }`         | Raw byte channel via native gRPC bidi stream                                           |
| `Optional[T]`                                      | `optional` field                                   | Proto3 optional                                                                        |

#### Leveraging Pydantic for type mapping

Rather than implementing the type mapping table from scratch, the builder leverages Pydantic's existing type introspection pipeline. Pydantic already has a complete type-to-schema system that handles all the types in the mapping table:

- **`BaseModel.model_json_schema()`** produces JSON Schema from any Pydantic model, automatically resolving `list[T]` → `{"type": "array", "items": ...}`, `Optional[T]` → `{"anyOf": [..., {"type": "null"}]}`, nested models → `$defs` with `$ref`, enums → `{"enum": [...]}`, etc.

- **`TypeAdapter(T).json_schema()`** works on arbitrary types (not just models), enabling introspection of `@export` method parameter types like `list[int]`, `Optional[str]`, or `UUID`.

- **`GenerateJsonSchema`** is Pydantic's extensible schema generator with ~55 type-specific handler methods (`int_schema()`, `str_schema()`, `list_schema()`, `model_schema()`, `enum_schema()`, etc.). By subclassing it, the builder can intercept type resolution and emit protobuf `FieldDescriptorProto` / `DescriptorProto` objects instead of JSON Schema dictionaries — reusing Pydantic's type walking, generic resolution, and forward reference handling.

The JSON Schema → protobuf mapping is mechanical:

| JSON Schema type                | Protobuf type                         |
| ------------------------------- | ------------------------------------- |
| `"integer"`                     | `TYPE_INT64`                          |
| `"number"`                      | `TYPE_DOUBLE`                         |
| `"string"`                      | `TYPE_STRING`                         |
| `"string"` + `"format": "uuid"` | `TYPE_STRING`                         |
| `"boolean"`                     | `TYPE_BOOL`                           |
| `"array"` + `"items"`           | `repeated` field                      |
| `"object"` + `"properties"`     | Generated `DescriptorProto` (message) |
| `"anyOf": [T, null]`            | `optional` field                      |
| `"enum"`                        | Proto `enum` type                     |

This approach means Pydantic handles ~80-85% of the type mapping automatically. The remaining protobuf-specific concerns — field number assignment, streaming semantics, `@exportstream` detection, `FileDescriptorProto` assembly, and package/import management — are handled by the builder's own logic.

### Build-time `.proto` generation

The codegen CLI uses a `build_file_descriptor()` library function to construct a `google.protobuf.descriptor_pb2.FileDescriptorProto` from an interface class, then renders it as human-readable `.proto` source. The builder is a pure function — it is **not** called by the exporter at runtime or by any import-time hook.

```python
from google.protobuf.descriptor_pb2 import (
    FileDescriptorProto,
    ServiceDescriptorProto,
    MethodDescriptorProto,
    DescriptorProto,
    FieldDescriptorProto,
)

def build_file_descriptor(interface_class, version="v1"):
    """Build a FileDescriptorProto from a Python interface class.

    Introspects @export-decorated methods, maps Python type annotations
    to protobuf field/message/service descriptors, and returns a
    self-contained FileDescriptorProto that fully describes the interface.
    """
    package = f"jumpstarter.interfaces.{interface_class.__name__.lower()}.{version}"
    fd = FileDescriptorProto(
        name=f"{interface_class.__name__.lower()}.proto",
        package=package,
        syntax="proto3",
    )
    fd.dependency.append("google/protobuf/empty.proto")

    service = ServiceDescriptorProto(name=interface_class.__name__)

    for name, method in _get_exported_methods(interface_class):
        sig = inspect.signature(method)
        call_type = _infer_call_type(method)
        params = [
            (p.name, p.annotation, p.default)
            for p in sig.parameters.values() if p.name != "self"
        ]
        return_type = sig.return_annotation

        # Build request/response message descriptors
        request_msg = _build_request_message(fd, name, params)
        response_msg = _build_response_message(fd, name, return_type)

        service.method.append(MethodDescriptorProto(
            name=_to_pascal_case(name),
            input_type=f".{package}.{request_msg.name}",
            output_type=f".{package}.{response_msg.name}",
            server_streaming=(call_type in (
                CallType.SERVER_STREAMING, CallType.BIDI_STREAMING)),
            client_streaming=(call_type == CallType.BIDI_STREAMING),
        ))

    fd.service.append(service)
    return fd
```

This produces the same `FileDescriptorProto` that `protoc` would generate from a hand-written `.proto` file.

### Custom Options and Doc Comments

Protobuf service and message definitions carry structure — method names, parameter types, streaming semantics — but out of the box they don't carry versioning metadata. Additionally, while the type mapping captures *what* a method does structurally, it doesn't capture *why* or *how* in human terms. This section addresses both gaps: a lightweight custom option for interface versioning, and systematic generation of proto comments from Python docstrings.

#### Interface Versioning

Interface versioning follows standard protobuf package-level versioning conventions. The version is encoded in the package name (e.g., `jumpstarter.interfaces.power.v1`) and the `--version` flag on the codegen CLI. Breaking changes to an interface require a new package version (`v1` → `v2`), and `buf breaking` enforces backward compatibility within a version.

This approach was chosen over a custom `interface_version` service option because:

- It follows the standard protobuf/Buf versioning convention that all gRPC tooling already understands
- It avoids custom annotations and the extraction logic they require
- `buf breaking` is purpose-built for detecting incompatible proto changes
- Proto contracts are either compatible or they're a new version — semver within a package version adds complexity without benefit

#### Custom Annotations

A shared `jumpstarter/annotations/annotations.proto` file defines custom options for interface-specific metadata:

```protobuf
syntax = "proto3";
package jumpstarter.annotations;

import "google/protobuf/descriptor.proto";

extend google.protobuf.FieldOptions {
  // Marks this field as a resource handle — a UUID string referencing
  // a client-negotiated stream via the Jumpstarter resource system.
  // See "Resource Handle Pattern" in Design Details.
  optional bool resource_handle = 50000;
}
```

Field number 50000 falls within the range reserved by protobuf for organization-internal use (50000–99999), avoiding collision with other projects or future protobuf additions.

Note that `@exportstream` methods (raw byte stream constructors) do not need a custom annotation. They are represented as bidirectional streaming RPCs with a `StreamData { bytes payload }` message type — this pattern is unambiguous and sufficient for codegen tools to infer the correct dispatch mechanism. The `StreamData` message is auto-generated into the proto package when any `@exportstream` method exists, enabling native gRPC bidi streaming for byte transport without relying on `RouterService.Stream`.

#### Doc comments from docstrings

Proto comments (lines starting with `//` immediately preceding a service, method, message, or field definition) are a first-class concept in the protobuf ecosystem. They're preserved in `FileDescriptorProto` source info, rendered by `protoc-gen-doc`, displayed by `grpcurl describe`, shown in Buf Schema Registry, and emitted as language-native doc comments by standard codegen plugins (`protoc-gen-java`, `protoc-gen-ts`, etc.). There's no need to duplicate them as custom options — the standard proto comment mechanism already flows through the entire toolchain.

The `build_file_descriptor()` builder and the codegen CLI extract docstrings from Python and emit them as proto comments:

- **Class docstrings** → comments above the `service` definition
- **Method docstrings** → comments above each `rpc` definition
- **Dataclass docstrings** → comments above the `message` definition
- **Field docstrings** (via attribute docstrings or `Annotated` metadata) → comments above each field

For the `FileDescriptorProto` specifically, these comments are stored in the `source_code_info` field, which is the standard protobuf mechanism for attaching comments to descriptor elements by path.

#### Python source example

```python
class PowerInterface(DriverInterface):
    """Control and monitor power delivery to a device under test.

    Provides on/off switching and real-time voltage/current monitoring
    for devices connected through a managed power relay.
    """

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_power.client.PowerClient"

    @abstractmethod
    async def on(self) -> None:
        """Energize the power relay, delivering power to the DUT.

        Idempotent: calling on() when already powered is a no-op.
        """
        ...

    @abstractmethod
    async def off(self) -> None:
        """De-energize the power relay, cutting power to the DUT."""
        ...

    @abstractmethod
    async def read(self) -> AsyncGenerator[PowerReading, None]:
        """Stream real-time power measurements from the DUT power rail."""
        ...


class PowerReading(BaseModel):
    """Real-time power measurement from the DUT power rail."""

    voltage: float
    """Measured rail voltage in volts."""

    current: float
    """Measured rail current in amperes."""
```

#### Generated `.proto` output

When the codegen CLI processes the class above, the resulting `.proto` file carries the version option and doc comments:

```protobuf
syntax = "proto3";
package jumpstarter.interfaces.power.v1;

import "google/protobuf/empty.proto";

// Control and monitor power delivery to a device under test.
//
// Provides on/off switching and real-time voltage/current monitoring
// for devices connected through a managed power relay.
service PowerInterface {
  // Energize the power relay, delivering power to the DUT.
  // Idempotent: calling on() when already powered is a no-op.
  rpc On(google.protobuf.Empty) returns (google.protobuf.Empty);

  // De-energize the power relay, cutting power to the DUT.
  rpc Off(google.protobuf.Empty) returns (google.protobuf.Empty);

  // Stream real-time power measurements from the DUT power rail.
  rpc Read(google.protobuf.Empty) returns (stream PowerReading);
}

// Real-time power measurement from the DUT power rail.
message PowerReading {
  // Measured rail voltage in volts.
  double voltage = 1;

  // Measured rail current in amperes.
  double current = 2;
}
```

The proto is clean and readable. The comments flow through standard `protoc` codegen plugins to produce language-native documentation — Javadoc for Java/Kotlin, TSDoc for TypeScript, `///` for Rust, docstrings for Python — without any custom options or annotation processing. A developer reading the `.proto` file sees a self-documenting interface contract. The package version (`v1`) provides the compatibility boundary, and `buf breaking` enforces backward-compatible evolution within a version.

#### How doc comments improve codegen

Because proto comments are a standard feature, every language's codegen plugin already handles them. For example, `protoc-gen-kotlin` produces:

```kotlin
/**
 * Energize the power relay, delivering power to the DUT.
 * Idempotent: calling on() when already powered is a no-op.
 */
suspend fun on() { ... }
```

And `protoc-gen-ts` produces:

```typescript
/**
 * De-energize the power relay, cutting power to the DUT.
 */
async off(): Promise<void> { ... }
```

This happens for free — no custom options, no custom codegen plugins, no annotation processing. A future Jumpstarter-specific `jmp codegen` wrapper could compose these standard stubs into DeviceClass-typed wrappers, inheriting the documentation from the proto comments.

#### Doc comment round-trip consistency

The interface check CLI validates doc comments bidirectionally:

- **Python → Proto:** Verifies that docstrings in the Python source appear as proto comments in the generated `.proto` file.
- **Proto → Python:** Verifies that proto comments in a hand-authored `.proto` file produce corresponding docstrings in the generated Python code.

This ensures documentation doesn't drift regardless of which direction the developer is working from.

### Codegen CLI (Python → Proto)

The codegen CLI introspects a Python interface class and produces a canonical `.proto` source file:

```bash
<codegen> \
  --package jumpstarter-driver-power \
  --interface PowerInterface \
  --version v1 \
  --output python/packages/jumpstarter-driver-power/proto/power/v1/power.proto
```

The `.proto` file is co-located with the driver package that defines the interface — not in the central `protocol/` directory, which is reserved for Jumpstarter's own wire protocol (`ExporterService`, `RouterService`, etc.). This keeps interface schemas alongside their implementations and avoids confusion between the Jumpstarter protocol and driver interface contracts.

Implementation: loads the interface class via `importlib`, calls `build_file_descriptor()` to produce the `FileDescriptorProto`, then renders it as human-readable `.proto` source text. Python snake_case method names are converted to PascalCase RPC names (e.g., `read_data_by_identifier` → `rpc ReadDataByIdentifier`), following standard proto conventions.

For batch processing of all in-tree drivers, the codegen CLI's batch mode:

```bash
<codegen> --all
```

walks `DriverInterfaceMeta._registry` (populated at import time) to discover all defined interfaces and generates `.proto` files into each driver package's `proto/` directory.

### Out-of-tree drivers

Out-of-tree driver packages — drivers maintained outside this repository — participate in the same `.proto` workflow as in-tree drivers. The supported path is build-time codegen: the maintainer runs the codegen CLI against their `DriverInterface` subclasses, commits the resulting `.proto` files into their package's `proto/` directory, and bundles a pre-compiled descriptor set produced by `protoc --descriptor_set_out` at the package's build time. Their `DriverInterface` subclasses register with `DriverInterfaceMeta._registry` automatically at import time, so the codegen CLI's batch mode picks them up once the package is installed in the development environment, and the interface check CLI can run against any importable interface module — out-of-tree packages are not a special case.

If an out-of-tree driver ships neither a committed `.proto` nor a bundled descriptor, the exporter logs a warning naming the driver and continues to load it. The driver still serves `DriverCall` traffic normally, so existing Python clients keep working. Three things degrade in that case:

- `DriverInstanceReport.file_descriptor_proto` is empty for that driver.
- gRPC reflection does not advertise the driver's interface.
- Polyglot (non-Python) clients that depend on the descriptor-set-in-report or reflection paths cannot discover the driver and will not be compatible until the maintainer ships a `.proto`.

The warning text should point to the codegen CLI and recommend adding it to the package's build so polyglot clients can consume the driver. This keeps the existing "easy driver development" property intact: authors can iterate without a `.proto` and add one when they're ready to support polyglot clients.

Auto-generating descriptors for out-of-tree drivers — for example by introspecting Python interfaces at exporter startup, or by compiling shipped `.proto` source on-demand without a pre-built descriptor — is deliberately out of scope for this JEP. This JEP commits to build-time codegen as the only supported path. A future JEP may revisit runtime auto-generation as a convenience for out-of-tree drivers if real-world friction warrants it.

### Client inheritance convention

This JEP firms up the Python client contract: a client class inherits from **both** its interface and `DriverClient`:

```python
# New convention
class PowerClient(PowerInterface, DriverClient):
    def on(self) -> None:
        self.call("on")
    def off(self) -> None:
        self.call("off")
    def read(self) -> Generator[PowerReading, None, None]:
        for raw in self.streamingcall("read"):
            yield PowerReading.model_validate(raw, strict=True)
```

In the current codebase, client classes inherit only from `DriverClient` (e.g., `class PowerClient(DriverClient)`). Dual inheritance gives type checkers a way to verify that every client method is actually declared on the interface — if a `DriverInterface` method is missing from the client, mypy / pyright will flag the subclass as incomplete. It also makes the client relationship to the interface explicit across languages that don't support multiple inheritance — those languages can fall back to single-inherit-from-interface with a `DriverClient` helper, but the contract is the same.

**Migration:** The standard in-tree clients (PowerClient, NetworkClient, StorageMuxClient, FlasherClient, CompositeClient, and the virtual-power client) are migrated to dual inheritance alongside the `DriverInterface` migration (Phase 1b). Drivers with clients that provide client-side orchestration (e.g., `FlasherClient` with `OpendalAdapter`, `StorageMuxFlasherClient.flash()`) keep their hand-written orchestration — dual inheritance does not change the methods, only the declared bases.

### Proto-first workflow (deferred)

An earlier revision of this JEP described a a proto-first codegen companion command that took a `.proto` file and generated a Python interface class, client class, and driver adapter. That capability is **not part of this JEP** and is deferred to a follow-up JEP focused on non-Python codegen.

Rationale:

- For Python-first drivers (the primary path in this repository), the proto-first adapter adds an extra inheritance layer and `@export`-on-`__method` indirection without reducing the code a driver developer writes. A driver author still writes the hardware logic in abstract methods; the adapter only relocates the `@export` decorator one class up.
- The main value of proto-first generation is for **non-Python** consumers — Kotlin, Java, TypeScript, Rust — which can already consume the committed `.proto` files via standard `protoc` plugins. A reference prototype for non-Python codegen exists and will be proposed in a follow-up JEP.
- Removing a proto-first codegen companion from this JEP shrinks the scope, unblocks the Python-first path, and avoids committing to an adapter pattern before non-Python codegen design is complete.

The `.proto` schema format defined by this JEP is stable enough that the follow-up JEP can build on it without revisiting the schema.

### Interface check CLI (CI drift detection)

Because the `.proto` files are committed and reviewed, CI needs a way to detect when a Python interface change makes the committed `.proto` stale. The interface check CLI is that gate:

```bash
<interface-check> \
  --proto python/packages/jumpstarter-driver-power/proto/power/v1/power.proto \
  --interface jumpstarter_driver_power.interface.PowerInterface
```

The tool runs `build_file_descriptor()` against the live Python class, parses the committed `.proto` file, and reports any mismatch in method names, parameter/return types, streaming semantics, or doc comments. It runs in CI alongside `buf breaking` — `buf breaking` detects backward-incompatible changes between old and new proto revisions; the interface check CLI detects drift between the current Python interface and the current proto revision. Together they cover both classes of failure.

**Discovery.** The check CLI accepts `--interface <module.path>` for single-interface use (the form shown above). For "check everything" CI runs, it walks `DriverInterfaceMeta._registry` — the same mechanism the codegen CLI's batch mode uses — so importing the package(s) under check is sufficient discovery. There is no separate yaml manifest of interfaces to keep in sync; the metaclass registry is the single source of truth.

### API / Protocol Changes

#### `DriverInstanceReport` Extension

A new `file_descriptor_proto` field is added to carry the serialized descriptor in each driver's report:

```protobuf
message DriverInstanceReport {
  string uuid = 1;
  optional string parent_uuid = 2;
  map<string, string> labels = 3;
  optional string description = 4;
  map<string, string> methods_description = 5;
  // Serialized google.protobuf.FileDescriptorProto for this driver's interface.
  // Contains complete service + message definitions.
  // Clients can parse this to discover methods, types, and streaming semantics
  // without a separate gRPC reflection call.
  optional bytes file_descriptor_proto = 6;  // NEW
}
```

This embeds the descriptor directly in the report, making `GetReport` self-describing. A Java client parses the bytes as `FileDescriptorProto`, feeds it to a `DescriptorPool`, and has full type information for every driver — method names, parameter types, return types, streaming semantics — without needing a separate gRPC reflection call.

**Source of the bytes.** The descriptors are loaded from a **pre-compiled descriptor set** produced by `protoc --descriptor_set_out` from the committed `.proto` files. The exporter reads this file once at startup and indexes the `FileDescriptorProto` by driver interface class. It does **not** run introspection at startup — that work is done at development time by the codegen CLI, and the output is committed as part of the driver package.

The field is `optional bytes` (not a nested message) because `FileDescriptorProto` is a well-known protobuf type that clients parse with their own language's descriptor library. Keeping it as raw bytes avoids adding `google/protobuf/descriptor.proto` as a direct dependency of the Jumpstarter protocol.

**This change is additive.** Old clients ignore the new field. Old exporters do not populate it.

#### gRPC Server Reflection

At exporter startup, the `Session` loads the committed descriptor set and registers each service with `grpcio-reflection`:

```python
from google.protobuf.descriptor_pb2 import FileDescriptorSet
from grpc_reflection.v1alpha import reflection

def register_reflection(server, descriptor_set_path):
    descriptor_set = FileDescriptorSet()
    with open(descriptor_set_path, "rb") as f:
        descriptor_set.ParseFromString(f.read())

    service_names = [reflection.SERVICE_NAME]
    for fd in descriptor_set.file:
        for service in fd.service:
            service_names.append(f"{fd.package}.{service.name}")

    reflection.enable_server_reflection(service_names, server)
```

This serves the descriptors through the standard `grpc.reflection.v1.ServerReflection` service, enabling standard tools (`grpcurl`, Postman, Java's `ProtoReflectionDescriptorDatabase`) to discover every driver interface on any exporter.

As noted in the Proposal, reflection in this JEP is **advisory**: services discovered via reflection describe the driver API but are not directly invocable — native gRPC handlers are a follow-up JEP. Standard tools can still use the reflected schema to generate typed stubs that drive `DriverCall` under the hood.

The `file_descriptor_proto` in the report and the gRPC reflection service serve the same data through different channels. The report embeds the descriptor for clients that want it inline with the driver tree. Reflection serves it through the standard gRPC mechanism for tools that expect that protocol. They are the same `FileDescriptorProto` — no duplication of schema definitions.

### Hardware Considerations

This JEP is a purely software-layer change. No hardware is required or affected. Introspection runs at development time inside the codegen CLI; the exporter itself reads a pre-compiled descriptor set once at startup. The `FileDescriptorProto` for a typical driver interface with 5–10 methods is approximately 1–3 KB serialized. Exporters running on resource-constrained SBCs (e.g., Raspberry Pi 4) should see no measurable runtime impact beyond one file read at startup.

## Design Decisions

### DD-1: Committed `.proto` files, not runtime introspection

**Alternatives considered:**

1. **Runtime dynamic `FileDescriptorProto` generation** — the exporter introspects `@export` methods at startup and builds descriptors on demand.
2. **Committed `.proto` files produced by the codegen CLI** — schemas are authored (via tool-assisted generation), committed to the driver package, compiled with `protoc --descriptor_set_out`, and loaded at startup.

**Decision:** Option 2 — committed `.proto` files.

**Rationale:** Committed schemas give reviewers a visible diff, CI a concrete artifact for `buf breaking`, and polyglot consumers a stable reference. Dynamic generation has no diff, couples dispatch to schema at import time, and shifts the drift-detection problem onto the exporter. An interface-check CI gate against a committed `.proto` is both simpler and more informative than runtime reconstruction.

### DD-2: Opt-in annotation validation, not mandatory

**Alternatives considered:**

1. **Mandatory at decoration time** — `@export` raises `TypeError` for any method without complete annotations. Forces the entire codebase (~111 methods across 25 packages) to be fully typed before anything builds.
2. **Opt-in via `@export(strict=True)` / `JMP_EXPORT_STRICT=1`** — `@export` in default mode emits `DeprecationWarning`. Teams enable strict mode per package. The codegen CLI always requires full annotations — enforcement moves to the tool.

**Decision:** Option 2 — opt-in.

**Rationale:** Mandatory enforcement blocks packages that don't need polyglot exposure and couples this JEP to a 111-method mechanical fix. Opt-in lets the ecosystem migrate incrementally while still guaranteeing annotation coverage for any interface that actually publishes a `.proto`.

### DD-3: Python-first only; proto-first deferred

**Alternatives considered:**

1. **Bidirectional tooling in Phase 1** — ship both the codegen CLI (Python → `.proto`) and a proto-first companion (`.proto` → Python interface + client + driver adapter).
2. **Python-first only** — ship only the codegen CLI and the interface check CLI. Proto-first is deferred to a follow-up JEP focused on non-Python codegen.

**Decision:** Option 2 — Python-first only.

**Rationale:** For Python drivers, the proto-first adapter pattern adds an inheritance layer and an underscore-prefixed abstract-method indirection without materially reducing the code the author writes. Its main value is producing clients and servicers for **non-Python** languages — that design is orthogonal to the Python introspection work and benefits from a dedicated JEP. Shrinking scope unblocks Phase 1 and avoids committing to a Python adapter pattern before non-Python codegen design is complete. A reference prototype for non-Python codegen already exists and will be the basis for the follow-up JEP.

### DD-4: Dual inheritance for generated and migrated clients

**Alternatives considered:**

1. **Keep single inheritance** — `class PowerClient(DriverClient)` — clients implement the interface by convention, not by declaration.
2. **Adopt dual inheritance** — `class PowerClient(PowerInterface, DriverClient)` — clients explicitly implement the interface; type checkers verify method coverage.

**Decision:** Option 2 — dual inheritance.

**Rationale:** Dual inheritance makes the client-to-interface relationship structural, not nominal. Type checkers flag missing interface methods on the client at analysis time; new clients inherit a typed contract by construction. This also firms up the semantics across languages — for languages without multiple inheritance, the equivalent is single-inherit-from-interface with a `DriverClient` helper.

### DD-5: Reflection is advisory in this JEP

**Alternatives considered:**

1. **Reflect and invoke** — register native gRPC handlers alongside reflection so that reflected services are directly invocable (e.g., via `grpcurl`).
2. **Reflect only** — register services for schema discovery, leave invocation on the native gRPC path as `UNIMPLEMENTED` until a follow-up JEP designs the native transport.

**Decision:** Option 2 — reflect only.

**Rationale:** Native gRPC handlers require a substantial design for UUID routing, dual-path dispatch during transition, and backward compatibility with legacy `DriverCall` clients. That design exists as a sketch (see "Native gRPC Transport — Design Sketch") but belongs in its own JEP. In the meantime, reflection is still valuable for codegen, documentation, and typed-stub generation — clients use reflected schemas to drive the existing `DriverCall` transport. The `UNIMPLEMENTED` behavior is documented explicitly in the Proposal and integration test suite.

## Design Details

### Architecture

```
  ┌────────────────────────────────────────────── development time ───┐
  │                                                                   │
  │  ┌────────────────────────────┐                                   │
  │  │   Python Interface Class   │  (PowerInterface, etc.)           │
  │  │   with @export methods     │                                   │
  │  └─────────────┬──────────────┘                                   │
  │                │  inspect.signature() (build-time only)           │
  │                ▼                                                  │
  │  ┌────────────────────────────┐                                   │
  │  │      codegen CLI           │                                   │
  │  │  (build_file_descriptor)   │                                   │
  │  └─────────────┬──────────────┘                                   │
  │                │  renders                                         │
  │                ▼                                                  │
  │  ┌────────────────────────────┐                                   │
  │  │  committed .proto file     │  (in driver package proto/ dir)   │
  │  └─────────────┬──────────────┘                                   │
  │                │  protoc --descriptor_set_out                     │
  │                ▼                                                  │
  │  ┌────────────────────────────┐                                   │
  │  │  descriptor set (bundled)  │                                   │
  │  └─────────────┬──────────────┘                                   │
  └────────────────┼──────────────────────────────────────────────────┘
                   │
  ┌────────────────┼────────────────────────── exporter runtime ──────┐
  │                ▼                                                  │
  │  ┌─────────────────────────────┐                                  │
  │  │ Session loads descriptor    │                                  │
  │  │ set once at startup         │                                  │
  │  └──┬────────────────┬─────────┘                                  │
  │     │                │                                            │
  │     ▼                ▼                                            │
  │  ┌──────────┐    ┌────────────────┐                               │
  │  │gRPC      │    │ DriverInstance │                               │
  │  │Reflection│    │ Report bytes   │                               │
  │  │(advisory)│    │(embedded)      │                               │
  │  └──────────┘    └────────────────┘                               │
  └───────────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **At development time:** The interface author runs the codegen CLI against a `DriverInterface` class. The tool calls `inspect.signature()` on each `@export` method, maps Python types to protobuf types, produces a `FileDescriptorProto`, and writes it out as a `.proto` source file under the driver package's `proto/` directory. The author reviews and commits the file. A build step runs `protoc --descriptor_set_out` to produce a binary descriptor set bundled with the package.

2. **In CI:** the interface check CLI runs on every change. It regenerates the descriptor from the current Python interface and compares it against the committed `.proto` file, failing if they diverge. `buf breaking` also runs to catch backward-incompatible changes.

3. **At exporter startup:** The `Session` loads the bundled descriptor set once, indexes `FileDescriptorProto` by interface class, registers service names with `grpc_reflection`, and retains the raw bytes for report embedding. No introspection happens at startup.

4. **At `GetReport` time:** Each `DriverInstanceReport` carries the `file_descriptor_proto` bytes for its interface. Clients parse them with their language's protobuf library to discover the full schema.

### `DriverInterfaceMeta` and `DriverInterface` — Type-Safe Interface Definitions

This JEP introduces a new metaclass + base class pair that provides type-safe, validated interface definitions, replacing the current convention of bare `ABCMeta`:

```python
# jumpstarter/driver/interface.py
from abc import ABCMeta, abstractmethod
from typing import ClassVar


class DriverInterfaceMeta(ABCMeta):
    """Metaclass for Jumpstarter driver interfaces.

    Enforces:
    - client() classmethod must be defined and return str
    Provides:
    - Interface registry for the codegen CLI's batch mode
    - Unambiguous discovery for build_file_descriptor()
    """
    _registry: ClassVar[dict[str, type]] = {}

    def __new__(mcs, name, bases, namespace, **kwargs):
        cls = super().__new__(mcs, name, bases, namespace, **kwargs)

        # Skip validation on the base DriverInterface class itself
        if name == "DriverInterface":
            return cls

        # Skip validation on intermediate abstract bases that don't
        # define their own client() (e.g., StorageMuxFlasherInterface
        # extending StorageMuxInterface)
        is_concrete_interface = "client" in namespace

        if is_concrete_interface:
            # Validate client() classmethod
            client_method = namespace.get("client")
            if client_method is None:
                raise TypeError(
                    f"{name} must define a client() classmethod "
                    f"returning the import path of the client class"
                )

            # Register the interface
            mcs._registry[f"{cls.__module__}.{cls.__qualname__}"] = cls

        return cls


class DriverInterface(metaclass=DriverInterfaceMeta):
    """Base class for all Jumpstarter driver interfaces.

    Subclass this to define a driver interface contract. All methods
    (except client()) must be @abstractmethod with full type annotations.

    Required:
        client(): classmethod returning the client import path
    """

    @classmethod
    @abstractmethod
    def client(cls) -> str:
        """Return the full import path of the corresponding client class."""
        ...
```

Interfaces migrate from `metaclass=ABCMeta` (or no metaclass) to inheriting `DriverInterface`:

```python
# Before:
from abc import ABCMeta, abstractmethod

class PowerInterface(metaclass=ABCMeta):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_power.client.PowerClient"
    @abstractmethod
    async def on(self) -> None: ...

# After:
from jumpstarter.driver import DriverInterface

class PowerInterface(DriverInterface):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_power.client.PowerClient"

    @abstractmethod
    async def on(self) -> None:
        """Energize the power relay, delivering power to the DUT."""
        ...
```

#### Complete interface migration list

The following interfaces require migration to `DriverInterface`. Each currently uses `metaclass=ABCMeta` unless otherwise noted:

| Interface                    | Package                        | Current State                  | Notes                                                              |
| ---------------------------- | ------------------------------ | ------------------------------ | ------------------------------------------------------------------ |
| `PowerInterface`             | `jumpstarter-driver-power`     | ABCMeta, fully typed           | Straightforward migration                                          |
| `VirtualPowerInterface`      | `jumpstarter-driver-power`     | ABCMeta, fully typed           | Separate from PowerInterface; `off(destroy: bool = False)` differs |
| `NetworkInterface`           | `jumpstarter-driver-network`   | ABCMeta                        | `connect()` missing return type annotation                         |
| `FlasherInterface`           | `jumpstarter-driver-opendal`   | ABCMeta                        | `flash(source)` and `dump(target)` missing param types             |
| `StorageMuxInterface`        | `jumpstarter-driver-opendal`   | ABCMeta                        | 5 methods missing return types                                     |
| `StorageMuxFlasherInterface` | `jumpstarter-driver-opendal`   | Inherits StorageMuxInterface   | No own methods; just overrides `client()`                          |
| `CompositeInterface`         | `jumpstarter-driver-composite` | **No metaclass (plain class)** | Empty interface, no abstract methods                               |

**Explicitly out of scope:** `FlasherClientInterface` (`jumpstarter-driver-opendal/client.py`) is a client-side ABC with complex types (`PathBuf`, `Operator`, `Compression`). It is not a driver interface contract and does not need migration to `DriverInterface`. The introspection system targets driver-side interfaces only.

Interface inheritance works naturally via Python MRO:

```python
class StorageMuxInterface(DriverInterface):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_opendal.client.StorageMuxClient"
    @abstractmethod
    async def host(self) -> None: ...
    @abstractmethod
    async def dut(self) -> None: ...

class StorageMuxFlasherInterface(StorageMuxInterface):
    # Inherits all methods from StorageMuxInterface
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_opendal.client.StorageMuxFlasherClient"
```

**Type safety enforced by the metaclass:**

- Missing `client()` → `TypeError` at class definition time
- Type checkers (mypy, pyright) see `client()` as required abstract classmethod

**Empty interfaces** (like `CompositeInterface`) work naturally — they inherit `DriverInterface`, define `client()`, and have no abstract methods. The builder produces an empty `ServiceDescriptorProto`. Note that `CompositeInterface` currently has no metaclass at all (it's a plain class, not even `ABCMeta`), so migration adds both the metaclass and `DriverInterface` base in one step.

**Deferred: `UdsInterface` concrete mixin.** The `UdsInterface` pattern — where `@export` is placed directly on the interface class without `ABCMeta` — is an anti-pattern that conflates the interface contract with the dispatch implementation. `UdsInterface` should eventually be refactored to use `DriverInterface` with `@abstractmethod`, with the shared `@export` implementations moved to a separate mixin class (e.g., `UdsDriverMixin`). However, this refactoring involves ~18 methods shared between `UdsCan` and `UdsDoip` via multiple inheritance, making it a non-trivial migration with code duplication risk. **This refactoring is deferred to a follow-up task** and is not a prerequisite for Phase 1b. The `build_file_descriptor()` builder can detect `@export` on non-`DriverInterface` classes and handle them via a legacy fallback path during the transition period.

**Discovery and registry:**

- `DriverInterfaceMeta._registry` automatically tracks all defined interfaces
- `build_file_descriptor()` checks `isinstance(cls.__class__, DriverInterfaceMeta)` for unambiguous discovery
- The codegen CLI's batch mode iterates the registry — no package entry-point scanning needed

**Migration:** Each interface changes from `metaclass=ABCMeta` to inheriting `DriverInterface`. Drivers that inherit from both the interface and `Driver` continue to work since `DriverInterfaceMeta` extends `ABCMeta`. The migration also requires adding full type annotations to all abstract methods — this is the forcing function for making the entire interface ecosystem type-safe.

### Opt-in type annotation enforcement for `@export`

Generating a proto from a Python interface requires every `@export` method to have complete type annotations. But most existing drivers predate this requirement, and forcing annotations on every `@export` method in the codebase at import time would turn this JEP into an ~111-method codebase audit blocking Phase 1.

Instead, this JEP introduces annotation validation as **opt-in**:

```python
def export(func=None, *, strict=False):
    """Decorator for exporting a method as a driver call.

    When strict=True (or the JMP_EXPORT_STRICT environment variable is set),
    the decorator raises TypeError at decoration time for any parameter or
    return type that lacks an annotation.

    Otherwise, missing annotations emit a DeprecationWarning but do not
    block import. The codegen and interface check CLIs will still refuse
    to produce a proto for an incompletely-typed interface — that is
    where the contract is enforced for polyglot consumption.
    """
    ...
```

Three enforcement tiers exist:

- **Permissive (default):** `@export` logs a `DeprecationWarning` for missing annotations. Existing drivers continue to import unchanged.
- **Strict (`@export(strict=True)` or `JMP_EXPORT_STRICT=1`):** `TypeError` at decoration time. Opt in per package when the team is ready.
- **Tool-level (non-negotiable):** The codegen CLI fails with a clear error if the interface has incompletely annotated methods — there is no way to emit a proto with unknown types. The interface check CLI inherits the same requirement.

Type enforcement is opt-in so it doesn't affect drivers that aren't yet consumed by polyglot clients. Teams that want the tighter contract enable strict mode package by package as they publish proto schemas.

**Annotation coverage in the current codebase.** An audit identified ~111 `@export` / `@exportstream` methods across 25 packages missing one or more annotations (mostly `-> None` return types on void methods, plus a handful of resource-handle `source` / `target` parameters). These fixes remain good practice and are recommended alongside Phase 1b, but they are **not blocking** for this JEP — packages migrate to fully-typed `@export` and emit proto schemas on their own schedule.

### Driver Patterns and Introspection Scope

Jumpstarter drivers follow several patterns in practice. The introspection and proto generation system must handle each one appropriately.

#### Pattern 1: Drivers with explicit interface classes (primary path)

The standard and most common pattern in the Jumpstarter ecosystem. A separate abstract interface class defines the contract, one or more driver classes implement it, and a client class provides the consumer API:

```
PowerInterface (abstract)      → PowerClient (DriverClient)
  ├── MockPower (Driver)
  ├── DutlinkPower (Driver)
  ├── TasmotaPower (Driver)
  ├── HttpPower (Driver)
  ├── EnerGenie (Driver)
  └── SNMPPower (Driver)
```

Every standard in-tree interface follows this pattern: `PowerInterface`, `NetworkInterface`, `FlasherInterface`, `StorageMuxInterface`, `StorageMuxFlasherInterface`, `CompositeInterface`. The interface class is the introspection target — `build_file_descriptor()` reads its abstract methods and type annotations to produce the `FileDescriptorProto`. This is the path the JEP is primarily designed for.

When a driver implements an explicit interface, the `@export`-decorated methods on the driver class must match the abstract methods on the interface (same names, compatible signatures). The introspection reads from the interface, not the driver, so the proto describes the *contract*, not the *implementation*. Multiple driver implementations (MockPower, DutlinkPower, TasmotaPower) all produce the same proto because they implement the same interface.

Interface inheritance also works naturally. `StorageMuxFlasherInterface` extends `StorageMuxInterface`, and the builder walks the MRO to collect all abstract methods from the full interface hierarchy into a single `ServiceDescriptorProto`.

#### Pattern 2: `@exportstream` methods (raw byte channels)

Some drivers use the `@exportstream` decorator instead of (or in addition to) `@export`. This creates a fundamentally different kind of interaction — a raw bidirectional byte stream tunneled through the `RouterService`, not a structured `DriverCall` RPC:

```python
# TcpNetwork driver — @exportstream for the byte channel
class TcpNetwork(NetworkInterface, Driver):
    @exportstream
    @asynccontextmanager
    async def connect(self):
        async with await connect_tcp(self.host, self.port) as stream:
            yield stream  # yields an anyio.abc.ObjectStream

    @export
    async def address(self):
        return f"tcp://{self.host}:{self.port}"
```

```python
# PySerial driver — @exportstream for the serial connection
class PySerial(Driver):
    @exportstream
    @asynccontextmanager
    async def connect(self):
        reader, writer = await open_serial_connection(url=self.url, ...)
        async with AsyncSerial(reader, writer) as stream:
            yield stream
```

The `@exportstream` methods are async context managers that yield raw byte streams. They are represented as native gRPC bidirectional streaming RPCs using a `StreamData { bytes payload }` message type that carries raw bytes. On the exporter, the generated servicer bridges between the gRPC bidi stream and the driver's byte stream. On the client side, non-Python clients call the native gRPC bidi endpoint directly and bridge it to local TCP/UDP sockets for port forwarding.

**Proto mapping for `@exportstream`:** The descriptor builder detects the `MARKER_STREAMCALL` attribute set by `@exportstream` and emits a bidi streaming RPC with `StreamData` — a simple message containing a `bytes payload` field. The `StreamData` message is auto-generated into the proto package:

```protobuf
service NetworkInterface {
  // Opens a bidirectional byte stream to the network endpoint.
  rpc Connect(stream StreamData) returns (stream StreamData);
}

// Byte payload for bidirectional stream methods (@exportstream).
message StreamData {
  bytes payload = 1;
}
```

Note that the `NetworkInterface` in the current codebase only defines `connect()` as an abstract method. The `address()` method that exists on some implementations (e.g., `TcpNetwork`, `WebsocketNetwork`) is a driver-level extension, not part of the interface contract, and is therefore not included in the proto.

Codegen tools (including the deferred non-Python codegen) infer the dispatch mechanism from the proto structure: a bidirectional streaming RPC with `StreamData` request and response is a raw byte stream constructor (`@exportstream`). The `StreamData` pattern is unambiguous — no custom annotation is needed.

For Python clients, the hand-written pattern under this JEP is:

```python
class NetworkClient(NetworkInterface, DriverClient):
    def connect(self):
        """Open a raw byte stream. Use as: with client.stream("connect") as s: ..."""
        return self.stream("connect")
```

Note that drivers which add `@export` methods beyond the interface contract (like `TcpNetwork.address()`) can mix typed RPC methods and stream constructor methods in the same driver class. However, only the methods declared in the `DriverInterface` subclass appear in the generated proto. Driver-level extensions are discoverable at runtime through the `DriverInstanceReport` but are not part of the interface contract.

The `resource_handle` field option is defined in `jumpstarter/annotations/annotations.proto` (see "Custom Annotations" above).

#### Pattern 3: Composite and nested drivers

Jumpstarter drivers form trees. A `Dutlink` board exposes a composite root with named children — `power` (PowerInterface), `storage` (StorageMuxFlasherInterface), `console` (serial) — each with its own UUID, interface, and client. The `GetReport` RPC returns this tree as a flat list of `DriverInstanceReport` entries linked by `parent_uuid`:

```
Dutlink (CompositeInterface, uuid=root)
├── power   (PowerInterface,              uuid=aaa, parent=root)
├── storage (StorageMuxFlasherInterface,   uuid=bbb, parent=root)
└── console (NetworkInterface,             uuid=ccc, parent=root)
```

**How introspection handles the tree:**

Each driver in the tree produces its own `FileDescriptorProto` based on its interface class. The `DriverInstanceReport` for each node carries its own `file_descriptor_proto` bytes. A client parsing the report gets a complete picture:

- `root` → empty service (CompositeInterface, no methods)
- `aaa` → PowerInterface service (On, Off, Read)
- `bbb` → StorageMuxFlasherInterface service (Host, Dut, Off, Write, Read, Flash, Dump)
- `ccc` → NetworkInterface service (Connect)

The tree structure is already encoded in the existing `uuid` / `parent_uuid` fields. The `file_descriptor_proto` field adds *what each node can do* alongside *where it sits in the tree*.

**CompositeInterface** defines no abstract methods — it's a pure container:

```python
class CompositeInterface(DriverInterface):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_composite.client.CompositeClient"
```

For proto introspection, it produces an empty `ServiceDescriptorProto` (a service with no methods). Its value is structural: it defines the tree root and its children. The generated client for a composite is a container with named accessors for its children:

```python
# Auto-generated composite client
class CompositeClient(CompositeInterface, DriverClient):
    def __getattr__(self, name):
        return self.children[name]
```

**Proxy drivers** (`Proxy` class) are transparent to introspection — they delegate `report()` and `enumerate()` to their target, so the proto describes the target driver's interface, not the proxy itself.

**Client tree reconstruction** works the same as today: `client_from_channel()` calls `GetReport()`, topologically sorts by `parent_uuid`, and instantiates client classes in dependency order. The `file_descriptor_proto` on each report is available for polyglot clients to discover the full typed API of every node in the tree.

**For native gRPC (future):** Each child driver registers its own native gRPC service on the exporter's server. The UUID routing interceptor dispatches to the correct instance. A Kotlin client leasing a Dutlink board would get three typed stubs — one for `PowerInterface`, one for `StorageMuxFlasherInterface`, one for `NetworkInterface` — each bound to the correct child UUID:

```kotlin
val report = stub.getReport(Empty.getDefaultInstance())
// Parse tree from reports, create typed stubs per child
val power = PowerInterfaceClient(channel, driverUuid = "aaa")
val storage = StorageMuxFlasherInterfaceClient(channel, driverUuid = "bbb")
val console = NetworkInterfaceClient(channel, driverUuid = "ccc")

power.on()
storage.host()
// console.connect() → bidirectional byte stream
```

#### Pattern 4: Client-side convenience methods

Historically, some client classes added methods that aren't in the interface contract. The canonical example is `PowerClient.cycle()`:

```python
# Legacy pattern — client-side composition (avoid going forward)
class PowerClient(DriverClient):
    def on(self) -> None:        # in PowerInterface
        self.call("on")
    def off(self) -> None:       # in PowerInterface
        self.call("off")
    def cycle(self, wait=2):     # NOT in PowerInterface — pure client-side logic
        self.off()
        time.sleep(wait)
        self.on()
```

`cycle()` composes `off()` + `sleep()` + `on()` on the client side and does not correspond to an `@export` method on the driver. This works for Python clients but invisibly forces every polyglot client (Kotlin, TypeScript, Rust, …) to re-derive the same composition, since `cycle()` is not part of the proto contract.

**Interfaces remain pure ABCs.** No concrete methods live on the interface itself. This keeps the language-neutral contract honest: every method declared on a `DriverInterface` corresponds to an RPC in the generated `.proto`, and nothing else.

**Move convenience methods to the driver side.** Going forward, simple convenience methods like `cycle()` should be promoted to first-class `@export` methods on the driver and declared on the interface. The recommended shape:

```python
# Recommended pattern — convenience method on the driver
class PowerInterface(DriverInterface):
    @abstractmethod
    def on(self) -> None: ...
    @abstractmethod
    def off(self) -> None: ...
    @abstractmethod
    def cycle(self, wait: float = 2.0) -> None: ...   # now part of the contract

class PowerDriver(Driver, PowerInterface):
    @export
    def cycle(self, wait: float = 2.0) -> None:
        self.off()
        time.sleep(wait)
        self.on()

class PowerClient(PowerInterface, DriverClient):
    def on(self) -> None:    self.call("on")
    def off(self) -> None:   self.call("off")
    def cycle(self, wait: float = 2.0) -> None:
        self.call("cycle", wait)
```

Putting `cycle()` on the wire gives it a proto entry, makes it reachable from every generated client, lets the driver implement it atomically (guarding against torn power transitions if the client crashes mid-cycle), and removes a class of subtle behavioral drift between Python and polyglot consumers. Reducing client-side logic is an explicit goal: the client should be a thin typed transport over the proto contract, not a layer with its own undeclared behavior. As part of the Phase 1b interface migration, simple composites like `cycle()` are migrated server-side.

**Keep on the client only when orchestration genuinely requires it.** A small set of drivers — primarily `NetworkInterface` and `FlasherInterface` / `StorageMuxFlasherInterface` — need real client-side orchestration that cannot be expressed across the wire: file hashing, compression negotiation, `OpendalAdapter` resource handle setup, byte-stream tunneling. Those clients keep their hand-written orchestration methods (`FlasherClient.flash()`, `StorageMuxFlasherClient.flash()`/`dump()`, console connect helpers, etc.). They are the exception, not the rule. When in doubt, push the composite to the driver.

#### Pattern 5: Resource handle methods

Some interfaces use resource handles — opaque identifiers representing client-side streams negotiated through the Jumpstarter resource system. The `FlasherInterface` and `StorageMuxInterface` are the primary examples:

```python
class FlasherInterface(DriverInterface):
    @abstractmethod
    def flash(self, source: str, target: str | None = None) -> None: ...
```

On the driver side, `source` is a resource UUID received via `DriverCall`. On the client side, the actual `flash()` method creates an `OpendalAdapter` context manager, negotiates a stream handle, and passes it to `self.call("flash", handle, target)`. This orchestration involves file hashing, compression negotiation, and operator selection — none of which can be expressed in protobuf.

On the wire, resource handles are UUIDs (strings) — they are passed as `string` parameters through `DriverCall`. The generated `.proto` represents these as `string` with a custom annotation `jumpstarter.annotations.resource_handle = true` on the field, signaling to codegen tools that this parameter is a resource reference, not a plain string.

The hand-written `FlasherClient` with its `OpendalAdapter` orchestration (file hashing, compression negotiation, stream setup) remains the supported Python client pattern. The proto-level `resource_handle` annotation is a hint for future non-Python codegen; the polyglot resource handle protocol (how Java / Kotlin clients negotiate a stream and obtain a UUID to pass) will be specified in a follow-up JEP alongside non-Python codegen.

This pattern affects: `FlasherInterface`, `StorageMuxInterface`, `StorageMuxFlasherInterface`, and the OpenDAL storage driver.

### Error Handling and Failure Modes

- **Missing type annotations:** In the default `@export` mode, a missing annotation emits a `DeprecationWarning` but does not block import. In strict mode (`@export(strict=True)` or `JMP_EXPORT_STRICT=1`), a missing annotation raises `TypeError` at decoration time. The codegen and interface check CLIs refuse to produce a proto for an incompletely annotated interface regardless of mode — see "Opt-in type annotation enforcement for `@export`" above.

- **Unsupported types:** Complex Python types that don't have a clean protobuf mapping (e.g., `Union[str, int]`, custom metaclasses) cause the codegen CLI to warn and fall back to `google.protobuf.Value`. A future JEP may introduce `oneof` support for `Union` types.

- **Circular references in dataclasses:** The builder detects cycles during recursive field introspection and raises a descriptive error inside the codegen CLI rather than entering infinite recursion.

- **Reflection registration failure:** If `grpcio-reflection` is not installed (it is an optional dependency), the exporter logs a warning and continues without reflection. The `file_descriptor_proto` field in the report is still populated.

- **Missing descriptor set at startup:** If the exporter cannot find the pre-compiled descriptor set bundled with the driver package, it logs a warning, skips reflection registration for that driver, and leaves `file_descriptor_proto` empty in the report. The driver still loads and serves `DriverCall` traffic normally — descriptor exposure is best-effort.

- **Proto parse failure in the interface check CLI:** If the committed `.proto` file is malformed, `protoc` (invoked as a subprocess) produces a standard error message. The check CLI surfaces this with context about which file failed, and CI fails the build.

### Concurrency and Thread-Safety

`build_file_descriptor()` is a pure function (no side effects, no mutation of inputs) and safe to call from any thread — but it is only called at codegen CLI invocation time, so concurrency is not relevant at runtime. The exporter's descriptor-set load is a single file read during startup before the gRPC server begins accepting connections. The gRPC reflection service is thread-safe by design (`grpcio-reflection` handles concurrent requests internally).

### Security Implications

gRPC Server Reflection exposes the full interface schema to any client that can reach the exporter's gRPC port. In Jumpstarter's architecture, the exporter is already behind the controller's authentication and lease system — only clients with a valid lease can dial the exporter. Reflection does not bypass this; it's registered on the same `grpc.Server` that serves `ExporterService` and inherits its transport security (mTLS via cert-manager).

The `file_descriptor_proto` bytes in the report are served through the authenticated `GetReport` RPC and carry no additional security concern.

## Test Plan

### Unit Tests

- **Type mapping:** Verify each Python type in the mapping table produces the correct protobuf field type. Parameterized tests covering `str`, `int`, `float`, `bool`, `bytes`, `None`, `dict`, `Any`, `Optional[T]`, `@dataclass`, `AsyncGenerator[T]`.
- **`build_file_descriptor()` output:** Verify the produced `FileDescriptorProto` has correct package name, service name, method count, method names, input/output types, and streaming flags for representative interface classes.
- **Round-trip consistency:** Generate a `FileDescriptorProto` from a Python interface, render it as `.proto` source, parse the source back, and verify the descriptors are semantically identical.
- **Edge cases:** Incompletely annotated methods (tool refuses to generate), `Optional` fields, recursive dataclasses, empty interfaces (`CompositeInterface`).
- **Doc comment extraction:** Verify that class, method, and field docstrings are captured in the `FileDescriptorProto`'s `source_code_info` and rendered as proto comments by the codegen CLI.
- **Package versioning:** Verify that the `--version` flag produces the correct package name suffix (e.g., `jumpstarter.interfaces.power.v1` vs `v2`).
- **`@exportstream` detection:** Verify that methods decorated with `@exportstream` are detected by `build_file_descriptor()` and emitted as bidi streaming methods with `StreamData` request/response types, distinct from `@export` methods.
- **Mixed `@export` / `@exportstream` interfaces:** Verify that an interface class containing both `@export` and `@exportstream` methods (like `TcpNetwork` with `address` + `connect`) produces a single `ServiceDescriptorProto` with correctly differentiated method types.
- **Opt-in strict mode:** Verify that `@export` in default mode emits `DeprecationWarning` for missing annotations, and in `strict=True` mode raises `TypeError`.

### Integration Tests

- **Reflection discovery:** Start an exporter with a known driver tree, connect with `grpcurl`, and verify that `grpcurl list` returns the expected service names and `grpcurl describe` returns correct method signatures. Verify that invoking a reflected method returns `UNIMPLEMENTED` (expected until the native-gRPC follow-up JEP).
- **Report introspection:** Lease a device, call `GetReport`, parse the `file_descriptor_proto` bytes, and verify they describe the correct interface.
- **Codegen CLI end-to-end:** Run the CLI against an installed driver package and verify the output `.proto` file is valid (passes `buf lint`) and matches the expected schema.
- **Interface check CLI end-to-end:** Introduce a deliberate mismatch between a committed `.proto` file and a Python interface and verify the tool detects and reports it; verify CI fails on the drift.
- **Descriptor set bundling:** Verify that the `protoc --descriptor_set_out` output bundled with a driver package loads correctly at exporter startup and produces the expected `file_descriptor_proto` bytes in the report.

### Hardware-in-the-Loop Tests

No HiL tests are required for this JEP. The introspection layer operates entirely on Python type metadata and protobuf descriptors; it does not interact with physical hardware.

### Manual Verification

- Point `grpcurl` at a running exporter with the new reflection service and verify interactive exploration works as expected.
- Use Buf Studio or Postman's gRPC support to connect to an exporter and verify the interface is browsable with full type information.
- Generate `.proto` files for several existing in-tree drivers (power, serial, storage-mux, adb) and review them for correctness and idiomatic proto style.

## Acceptance Criteria

- [ ] `DriverInterfaceMeta` + `DriverInterface` base class ship and pass type-checker validation (`mypy`, `pyright`).
- [ ] Standard in-tree interfaces (Power, VirtualPower, Network, Flasher, StorageMux, StorageMuxFlasher, Composite) inherit `DriverInterface`; corresponding clients adopt dual inheritance.
- [ ] `@export` supports `strict=True` and `JMP_EXPORT_STRICT=1`; default mode emits `DeprecationWarning` for missing annotations.
- [ ] The codegen CLI produces `.proto` files that pass `buf lint` for every standard in-tree interface, with doc comments extracted from docstrings.
- [ ] The interface check CLI detects a deliberate mismatch in CI and fails the build.
- [ ] Committed `.proto` files and `protoc --descriptor_set_out` artifacts exist for each standard in-tree interface; the artifacts are bundled with the driver package.
- [ ] Exporter loads the bundled descriptor set at startup, registers reflection, and populates `DriverInstanceReport.file_descriptor_proto`.
- [ ] `grpcurl list` and `grpcurl describe` return the expected service names and method signatures against a running exporter; invoking a reflected method returns `UNIMPLEMENTED` as documented.
- [ ] `jumpstarter/annotations/annotations.proto` is published and importable by external `.proto` files.
- [ ] `DriverCall` / `StreamingDriverCall` wire protocol is byte-for-byte unchanged — a client from before this JEP connects to an exporter that includes this JEP without modification.

## Graduation Criteria

### Experimental

- The codegen CLI produces `.proto` files that pass `buf lint` for all in-tree interfaces.
- Generated `.proto` files include doc comments extracted from Python docstrings.
- Committed `.proto` files exist for all standard in-tree interfaces (Power, Network, StorageMux, Flasher, Composite).
- The `file_descriptor_proto` field is populated in `DriverInstanceReport` on at least one CI-connected exporter, loaded from the bundled descriptor set.
- The interface check CLI runs in CI and detects a deliberately introduced drift.
- At least one non-Python client (e.g., a Kotlin prototype or a `grpcurl describe` script) successfully discovers a driver interface using only the proto schema.
- `jumpstarter/annotations.proto` is published and importable by external `.proto` files.

### Stable

- The type mapping table is finalized and documented.
- The interface check CLI runs in CI for all in-tree drivers, catching any drift between `.proto` files and Python interfaces — including doc comment and version drift.
- At least one downstream JEP (DeviceClass, non-Python codegen, or Registry) has been implemented using the `.proto` artifacts from this JEP.
- No breaking changes to `jumpstarter/annotations.proto` for at least one release cycle.

## Backward Compatibility

This JEP is **fully backward compatible.** All changes are additive:

- The `file_descriptor_proto` field (field number 6) is added to `DriverInstanceReport` as `optional bytes`. Old clients using generated stubs from the current `.proto` definition will simply ignore the unknown field — this is standard protobuf behavior. Old exporters will not populate the field, and clients must handle its absence.

- gRPC Server Reflection is a separate service (`grpc.reflection.v1.ServerReflection`) registered alongside `ExporterService`. It is invisible to clients that don't query it. No existing RPCs are modified. Reflected services return `UNIMPLEMENTED` when invoked directly — a known limitation scheduled for removal in the native-gRPC follow-up JEP.

- The `@export` decorator is unchanged in its dispatch behavior. Existing markers, dispatch logic, and call semantics are untouched. The only addition is opt-in annotation validation (`strict=True` or `JMP_EXPORT_STRICT=1`), which is off by default.

- The codegen and interface check CLIs are new commands. They do not modify any existing commands.

- The `DriverCall` and `StreamingDriverCall` wire protocol is completely unchanged. The exporter still resolves method names as strings and serializes arguments as `google.protobuf.Value`. The committed `.proto` files describe the interface but do not replace the dispatch path. Migrating to native protobuf service implementations is explicitly out of scope for this JEP (see "Wire Protocol: `DriverCall` Remains Unchanged" in the Proposal).

- Proto-first (authoring `.proto` files and generating Python scaffolding) is out of scope; existing Python-first drivers are unaffected.

## Consequences

### Positive

- Polyglot clients (Kotlin, Java, TypeScript, Rust) gain a standards-based path to discover Jumpstarter driver APIs and generate typed stubs without reading Python source.
- Committed `.proto` files create a reviewable, diff-able artifact for interface changes; `buf breaking` detects backward-incompatible evolution automatically.
- The interface check CLI prevents silent drift between Python interfaces and their published schemas.
- Opt-in type enforcement lets teams tighten their `@export` contract at their own pace while the tool enforces fully-typed interfaces at publication time.
- Client dual inheritance gives type checkers a way to verify interface conformance without changing dispatch.
- The existing `DriverCall` wire protocol is untouched, so every existing client, driver, and deployment continues to work.

### Negative

- The `.proto` files are now a source artifact that must be kept in sync with Python interfaces. The interface check CI gate surfaces drift clearly, but authors take on responsibility for regenerating and committing `.proto` when they change an interface.
- `grpcio-reflection` becomes an optional dependency; installations without it lose the reflection convenience (though the descriptor-set-in-report path still works).
- Reflection advertises services that return `UNIMPLEMENTED` until the native-gRPC follow-up JEP lands. This is documented, but it is a known rough edge for operators pointing `grpcurl` at an exporter and expecting direct invocation.
- Adding `DriverInterface` and migrating standard in-tree interfaces is a non-trivial PR touching multiple driver packages.

### Risks

- **Scope creep.** "Proto-first for Python" is a tempting extension — a contributor might add a small code generator later that re-enters the territory this JEP explicitly left out. The follow-up non-Python codegen JEP needs to land first and set the pattern.
- **Annotation migration stalls.** Opt-in enforcement is safer but means a package can live indefinitely in a half-annotated state. Mitigation: the codegen CLI refuses incomplete interfaces, so publishing a proto forces completion.
- **Native-gRPC follow-up slips.** If the follow-up JEP takes longer than expected, the `UNIMPLEMENTED` reflection footgun persists. Mitigation: include a clear note in the exporter logs and in any `grpcurl` documentation.

## Rejected Alternatives

### Custom schema message instead of `FileDescriptorProto`

An earlier draft considered a custom `InterfaceSchema` protobuf message with fields for method names, parameter lists, and return types. This was rejected because:

- It would require custom parsers in every target language, whereas `FileDescriptorProto` is already understood by every protobuf library.
- It would not integrate with standard gRPC tooling (`grpcurl`, Buf, Postman) that expects `FileDescriptorProto` from reflection.
- It would create a second schema format alongside `.proto` files, doubling the maintenance surface.
- Protobuf's self-description mechanism is purpose-built for exactly this use case.

### JSON Schema instead of protobuf descriptors

JSON Schema was considered for maximum accessibility. It was rejected because:

- Jumpstarter's wire protocol is gRPC/protobuf; adding JSON Schema would introduce a second serialization format without clear benefit.
- JSON Schema cannot express gRPC-specific concepts (streaming semantics, service definitions) without custom extensions.
- `FileDescriptorProto` is already JSON-serializable via `protobuf.json_format` for clients that need JSON.

### Generating `.proto` files at build time via `protoc` plugin

A `protoc` plugin approach was considered, where a custom plugin would read Python AST and emit `.proto` files during `pip install`. This was rejected because:

- It inverts the dependency: `protoc` would need to parse Python, which is not its strength.
- It requires `protoc` to be installed in the development environment, adding a native dependency.
- The `build_file_descriptor()` approach is pure Python, runs at codegen CLI invocation time, and requires no external tooling beyond `protoc --descriptor_set_out` at build time.

### Storing type info in `methods_description` strings

Encoding type information into the existing `methods_description` map (e.g., as a JSON string per method) was considered. This was rejected because:

- It's a hack that conflates human-readable documentation with machine-readable schema.
- It doesn't integrate with any existing tooling.
- The `file_descriptor_proto` field is the proper place for machine-readable schema, and `methods_description` remains for human consumption.

### Runtime dynamic `FileDescriptorProto` generation at exporter startup

An earlier revision of this JEP (seen in the initial PR discussion) had the exporter construct `FileDescriptorProto` objects dynamically at startup by introspecting `@export` method signatures — with type metadata captured on each function at import time (`MARKER_TYPE_INFO`, `ExportedMethodInfo`). This was rejected in favor of committed `.proto` files produced by the codegen CLI because:

- **No reviewable artifact.** Dynamic generation produces no diff at review time. A signature change silently alters the wire schema; polyglot consumers get no CI signal until something breaks.
- **Import-time cost and coupling.** Storing `ExportedMethodInfo` on every `@export` function couples dispatch to schema, lengthens import, and bloats memory for drivers that don't need polyglot exposure.
- **Drift detection is simpler without it.** The interface check CLI diffs the live Python class against the committed `.proto`, catching drift directly and deterministically. A dynamic approach would have to diff against a previous run — requiring a lockfile that is effectively the committed `.proto` by another name.
- **Committed `.proto` files are the standard protobuf workflow.** `protoc`, `buf`, `grpcurl`, `buf breaking`, and every language's polyglot codegen pipeline expect a committed `.proto` source. Taking the standard path keeps the exporter free of schema-construction work and lets every existing tool participate.

Runtime introspection remains available for development-time tooling (the codegen CLI), but it is no longer part of the exporter's runtime path.

## Prior Art

- **gRPC Server Reflection** ([grpc.io/docs/guides/reflection](https://grpc.io/docs/guides/reflection/)) — the standard mechanism for runtime service discovery in gRPC. This JEP uses the exact same `FileDescriptorProto` format and `ServerReflection` service definition.

- **Buf Schema Registry** ([buf.build](https://buf.build/)) — a hosted registry for protobuf schemas. Jumpstarter's codegen CLI produces `.proto` files that are compatible with Buf's lint, breaking-change detection, and registry tooling.

- **Kubernetes Custom Resource Definitions (CRDs)** — Kubernetes uses OpenAPI v3 schemas embedded in CRDs for the same purpose: making API resources self-describing. Jumpstarter's approach is analogous but uses protobuf's native self-description mechanism instead of OpenAPI.

- **LAVA (Linaro Automated Validation Architecture)** — LAVA uses device type definitions and Jinja2 templates to describe hardware capabilities. Jumpstarter's approach is more strongly typed (protobuf vs. YAML templates) but serves the same goal of making device capabilities machine-discoverable.

- **Robot Framework Remote Library Interface** — Robot Framework's remote library protocol uses XML-RPC with `get_keyword_names` and `get_keyword_arguments` introspection. This JEP serves a similar purpose but uses a modern, strongly-typed, multi-language format.

## Unresolved Questions

### Must resolve before acceptance

1. **Field number assignment for `file_descriptor_proto`:** ~~Field number 6 is proposed. Need to confirm no in-flight PRs are using field 6 in `DriverInstanceReport`.~~ **Resolved:** Field 6 is already defined in `protocol/proto/jumpstarter/v1/jumpstarter.proto` as `optional bytes file_descriptor_proto = 6`.

2. **`grpcio-reflection` as required vs. optional dependency:** ~~Hard dependency or optional extra?~~ **Resolved:** Optional extra (`pip install jumpstarter[reflection]`). The exporter loads the bundled descriptor set regardless; reflection is advisory for tooling, not required for `DriverCall` dispatch. Keeping it optional reduces install size on constrained exporters.

3. **Proto package naming convention:** The proposed convention is `jumpstarter.interfaces.{name}.{version}` (e.g., `jumpstarter.interfaces.power.v1`). Should this be formalized as a requirement for all interfaces, or should driver authors have flexibility?

4. **`UdsInterface` refactoring:** ~~The `UdsInterface` concrete mixin pattern (where `@export` is on the interface itself) must be refactored to use `DriverInterface` + `@abstractmethod`. Should this refactoring be a prerequisite for JEP-0011, or tracked as a separate cleanup?~~ **Resolved:** Deferred to a follow-up task. `UdsInterface` is excluded from Phase 1b migration. The builder will handle non-`DriverInterface` classes via a legacy fallback path during the transition. See "Deferred: `UdsInterface` concrete mixin" in Design Details.

5. **Migration timeline for `DriverInterfaceMeta`:** ~~Should all existing interfaces migrate to the new `DriverInterface` base class in Phase 1, or can migration be gradual?~~ **Resolved:** All standard interfaces (PowerInterface, VirtualPowerInterface, NetworkInterface, FlasherInterface, StorageMuxInterface, StorageMuxFlasherInterface, CompositeInterface) migrate in Phase 1b. UdsInterface is deferred. FlasherClientInterface (a client-side ABC) is explicitly out of scope.

### Can wait until implementation

6. **`Union` type mapping:** How should `Union[str, int]` map to protobuf? `oneof` is the natural choice but adds complexity. Deferring to a future JEP is acceptable since `Union` is rarely used in current driver interfaces.

7. **Bidirectional streaming mapping:** The `@export` decorator supports `STREAM` (bidirectional) in addition to `UNARY` and `SERVER_STREAMING` — the TCP driver already uses bidirectional streaming. The proto mapping for bidirectional streaming (`stream → stream`) needs finalizing in `build_file_descriptor()`. This is required for completeness but can be added after unary and server-streaming support is stable.

8. **Proto style guide:** Should generated `.proto` files follow Google's style guide, Buf's style guide, or a Jumpstarter-specific convention? This affects field naming (snake_case vs. camelCase) and file organization.

9. **Docstring format for proto comments:** Should the builder strip reStructuredText or Google-style docstring directives (`:param:`, `Args:`, `Returns:`) before emitting proto comments, or pass them through verbatim? Stripping produces cleaner proto but loses structured parameter documentation.

10. **Resource handle annotation in Phase 1:** The `jumpstarter.annotations.resource_handle = true` field option is specified by this JEP, but its consumer (non-Python codegen that understands how to negotiate resource streams) lands in a follow-up. Should the annotation ship in Phase 5 anyway so committed `.proto` files already carry it, or wait until the polyglot resource protocol is designed?

11. **Pydantic model features beyond simple fields:** Pydantic models can have validators, computed properties (`apparent_power` on `PowerReading`), model config, and custom serialization. The builder introspects `model_fields` only — validators and computed properties are not represented in the proto. Is this acceptable, or should computed properties be surfaced as read-only fields?

## Future Possibilities

The following are **not** part of this JEP but are natural extensions enabled by it:

- **DeviceClass contracts and structural enforcement:** With machine-readable interface schemas, a `DeviceClass` CRD can reference specific interfaces and the controller can validate exporters against the contract — not just by checking labels, but by comparing actual `FileDescriptorProto` descriptors. Today, a driver declares that it implements `PowerInterface` by inheriting from the class, but there is no runtime or registration-time verification that the driver's `@export` methods actually match the interface contract. A typo in a method name, a missing parameter, or a wrong return type silently breaks clients at call time. The `FileDescriptorProto` from this JEP enables structural enforcement at every level of the DeviceClass mechanism:

  *At build time:* The interface check CLI verifies that a Python interface matches its `.proto` definition. This extends to verifying that a driver implementation's `@export` methods match the interface proto — catching signature mismatches before code is shipped.

  *At exporter registration time:* The controller receives `FileDescriptorProto` descriptors in each driver's `DriverInstanceReport`. It compares these against the canonical `FileDescriptorProto` stored in a DeviceClass or InterfaceClass CRD to perform structural validation — comparing actual method signatures, parameter types, return types, and streaming semantics. A driver that claims to implement `power-v1` but is missing the `read()` streaming method would be flagged at registration, not discovered at test time.

  *At lease time:* A lease requesting a specific DeviceClass resolves to a set of required interface references, each with a canonical proto. The controller validates that every matched exporter's drivers produce compatible descriptors — ensuring that the leased device actually satisfies the contract the test code was generated against.

  *For driver certification:* A DeviceClass could declare compliance requirements: "this device provides `power-v1` at version `1.0.0` with these exact method signatures." A future registry could track which driver packages are certified against which interface versions, and `jmp validate` could verify local exporter configurations against the published DeviceClass contract before deployment.

  The strongly-typed protos from this JEP make all of this structural rather than convention-based. Instead of relying on class inheritance and label matching (which can drift silently), the system compares machine-readable schemas at every boundary.

- **Polyglot client code generation:** The `.proto` files produced by the codegen CLI feed directly into `protoc` for Kotlin, TypeScript, Rust, and other language stubs. A `jmp codegen` tool could wrap this pipeline.

- **Driver registry:** A controller-level registry that catalogs available drivers, interfaces, and DeviceClasses — serving `FileDescriptorProto` artifacts for codegen and reflection.

- **Interface versioning and compatibility checking:** Using `buf breaking` against committed `.proto` files to enforce backward-compatible interface evolution across releases.

- **Dynamic client construction:** A "generic driver client" that uses `FileDescriptorProto` and `DynamicMessage` to invoke any driver method without pre-generated stubs — useful for debugging, REPL exploration, and ad-hoc tooling.

- **Additional custom options:** If the community identifies metadata that genuinely needs to be machine-readable beyond what proto comments provide (e.g., units of measurement, timing constraints, safety classifications), new options can be added to `jumpstarter/annotations.proto` via a follow-up JEP without changing the core introspection mechanism.

- **Interactive API documentation:** A web UI (served by the controller or Buf Schema Registry) that renders the `.proto` files as browsable, searchable API docs — similar to Swagger/OpenAPI but for gRPC driver interfaces, with proto comments displayed inline.

- **Native protobuf wire protocol (future JEP):** The `.proto` files produced by this JEP are the foundation for migrating from string-based `DriverCall` dispatch to native gRPC services. A detailed design sketch follows.

### Native gRPC Transport — Design Sketch

#### What changes

Today, every driver call flows through a single generic RPC:

```
Client                              Exporter
  │                                    │
  │  DriverCall(uuid, "on", [])        │
  │ ──────────────────────────────────>│
  │         encode_value → Value       │  lookup method by string
  │                                    │  decode_value(args)
  │  DriverCallResponse(result)        │  call method
  │ <──────────────────────────────────│  encode_value(result)
```

With native gRPC, each interface becomes a real service with compiled stubs:

```
Client                              Exporter
  │                                    │
  │  PowerInterface.On(Empty)          │
  │  metadata: driver-uuid=abc-123     │
  │ ──────────────────────────────────>│
  │         compiled protobuf msg      │  interceptor routes by UUID
  │                                    │  typed deserialization
  │  Empty                             │  call method directly
  │ <──────────────────────────────────│  typed serialization
```

The key differences:
- **No string dispatch:** gRPC resolves the method from the service/method path (`/jumpstarter.interfaces.power.v1.PowerInterface/On`)
- **No Value round-trip:** Arguments are compiled protobuf messages, not JSON-via-`google.protobuf.Value`
- **Standard per-method observability:** gRPC interceptors, tracing, and metrics work at the method level
- **UUID routing via metadata:** The `x-jumpstarter-driver-uuid` header replaces the UUID field in `DriverCallRequest`

#### Proto: what gets compiled

The `.proto` files from the codegen CLI (this JEP) are compiled by `protoc` to produce native stubs. For `PowerInterface`:

```protobuf
syntax = "proto3";
package jumpstarter.interfaces.power.v1;

import "google/protobuf/empty.proto";

service PowerInterface {
  rpc On(google.protobuf.Empty) returns (google.protobuf.Empty);
  rpc Off(google.protobuf.Empty) returns (google.protobuf.Empty);
  rpc Read(google.protobuf.Empty) returns (stream PowerReading);
}

message PowerReading {
  double voltage = 1;
  double current = 2;
}
```

`protoc` generates:
- **Python:** `power_pb2.py` (messages), `power_pb2_grpc.py` (stubs + servicers)
- **Java/Kotlin:** `PowerInterfaceGrpc.java`, `Power.java` (messages)
- **Go:** `power_grpc.pb.go`, `power.pb.go`
- etc.

#### Server side: driver as native gRPC servicer

Today, `Driver` implements `ExporterServiceServicer` and dispatches via `__lookup_drivercall`. With native gRPC, each driver also implements its interface's generated servicer:

```python
# Auto-generated by a proto-first codegen companion (or hand-written)
from jumpstarter.interfaces.power.v1 import power_pb2, power_pb2_grpc

class PowerServicer(power_pb2_grpc.PowerInterfaceServicer):
    """Bridges a PowerInterface driver to its native gRPC servicer."""

    def __init__(self, driver: PowerInterface):
        self._driver = driver

    async def On(self, request, context):
        await self._driver.on()
        return empty_pb2.Empty()

    async def Off(self, request, context):
        await self._driver.off()
        return empty_pb2.Empty()

    async def Read(self, request, context):
        async for reading in self._driver.read():
            yield power_pb2.PowerReading(
                voltage=reading.voltage,
                current=reading.current,
            )
```

The servicer is a thin adapter — it deserializes the compiled protobuf request, calls the driver method, and serializes the response. No `encode_value` / `decode_value`, no string lookup.

#### Duplicate instances: UUID routing interceptor

A single exporter can host multiple drivers implementing the same interface (e.g., `main_power` and `aux_power` both implementing `PowerInterface`). gRPC services are singletons — you can't register two `PowerInterfaceServicer` instances.

The solution is a server interceptor that reads the driver UUID from gRPC metadata and dispatches to the correct instance:

```python
class DriverRoutingInterceptor(grpc.aio.ServerInterceptor):
    """Routes native gRPC calls to the correct driver instance by UUID."""

    def __init__(self, session: Session):
        self.session = session
        # Map: (service_name, method_name) -> {uuid: servicer}
        self._servicers: dict[str, dict[UUID, grpc.GenericRpcHandler]] = {}

    def register(self, uuid: UUID, servicer, service_name: str):
        self._servicers.setdefault(service_name, {})[uuid] = servicer

    async def intercept_service(self, continuation, handler_call_details):
        # Extract UUID from metadata
        metadata = dict(handler_call_details.invocation_metadata)
        uuid_str = metadata.get("x-jumpstarter-driver-uuid")
        if uuid_str is None:
            # No UUID header — fall through to legacy DriverCall
            return await continuation(handler_call_details)

        # Route to the correct driver's servicer
        service_name = handler_call_details.method.rsplit("/", 2)[1]
        servicers = self._servicers.get(service_name, {})
        servicer = servicers.get(UUID(uuid_str))
        if servicer is None:
            return None  # gRPC returns UNIMPLEMENTED
        return servicer
```

#### Session registration

At exporter startup, the `Session` registers both the legacy `ExporterService` and native gRPC services:

```python
async def serve_async(self, server):
    # Legacy dispatch (unchanged)
    jumpstarter_pb2_grpc.add_ExporterServiceServicer_to_server(self, server)
    router_pb2_grpc.add_RouterServiceServicer_to_server(self, server)

    # Native gRPC services (new)
    interceptor = DriverRoutingInterceptor(self)
    for uuid, parent, name, driver in self.root_device.enumerate():
        servicer = build_native_servicer(driver)  # creates PowerServicer etc.
        if servicer is not None:
            interceptor.register(uuid, servicer, servicer.service_name)

    # The interceptor is passed to grpc.aio.server(interceptors=[interceptor])
```

#### Server side: `@export` during transition

During the dual-path transition period, driver methods retain their `@export` decorators. The legacy `DriverCall` path still needs them for string-based dispatch. The native `PowerServicer` adapter calls the same underlying methods — both paths converge on the same driver implementation:

```python
class MockPower(PowerInterface, Driver):
    @export  # Still needed for legacy DriverCall dispatch
    async def on(self) -> None:
        self.logger.info("power on")

    @export
    async def off(self) -> None:
        self.logger.info("power off")
```

Once `DriverCall` is removed (migration phase 4), the `@export` decorators become unnecessary for dispatch — but they continue to serve as the type introspection mechanism for `build_file_descriptor()` and `ExportedMethodInfo` capture.

#### Client side: `DriverClient` auto-generates native stubs

The `DriverClient` base class handles native stub creation automatically. When a driver's `DriverInstanceReport` includes a `file_descriptor_proto` and the exporter supports native gRPC, `DriverClient` creates the compiled stub internally — individual client classes don't need manual wiring:

```python
class AsyncDriverClient(Metadata):
    """Base class — auto-creates native stub when available."""

    async def _init_native_stub(self):
        """Called during client setup if FileDescriptorProto is present."""
        if self._file_descriptor_proto is None:
            return  # Legacy-only exporter, use DriverCall path

        # Build stub from compiled service descriptor + UUID interceptor
        intercepted_channel = grpc.intercept_channel(
            self._channel,
            UuidMetadataInterceptor(self.uuid),
        )
        self._native_stub = self._build_stub(intercepted_channel)

    async def call_async(self, method, *args):
        """Prefers native stub if available, falls back to DriverCall."""
        if self._native_stub is not None:
            return await self._call_native(method, *args)
        # Legacy path (unchanged)
        request = jumpstarter_pb2.DriverCallRequest(
            uuid=str(self.uuid), method=method,
            args=[encode_value(arg) for arg in args],
        )
        response = await self.stub.DriverCall(request)
        return decode_value(response.result)
```

The generated client code stays clean — it calls `self.call("on")` as before, and the base class routes to the native stub transparently:

```python
# Generated client — unchanged from DriverCall era
class PowerClient(PowerInterface, DriverClient):
    def on(self) -> None:
        self.call("on")  # DriverClient routes to native stub if available

    def off(self) -> None:
        self.call("off")

    def read(self) -> Generator[PowerReading, None, None]:
        for raw in self.streamingcall("read"):
            yield PowerReading.model_validate(raw, strict=True)
```

For non-Python clients, the compiled stubs are used directly with standard gRPC patterns:

```kotlin
// Kotlin — standard gRPC stub with metadata
val channel = ManagedChannelBuilder.forAddress(host, port).build()
val interceptor = UuidMetadataInterceptor("abc-123")
val stub = PowerInterfaceGrpcKt.PowerInterfaceCoroutineStub(channel)
    .withInterceptors(interceptor)

stub.on(Empty.getDefaultInstance())
stub.read(Empty.getDefaultInstance()).collect { reading ->
    println("Voltage: ${reading.voltage}V, Current: ${reading.current}A")
}
```

#### Backward compatibility: dual-path dispatch

During the transition, the exporter serves both protocols simultaneously:

- **Legacy path:** `ExporterService.DriverCall(uuid, "on", [])` — string dispatch with `Value` serialization. Existing Python clients continue to work.
- **Native path:** `PowerInterface.On(Empty)` + `x-jumpstarter-driver-uuid` metadata — compiled protobuf. New and polyglot clients use this.

Both paths call the same underlying driver methods. The driver implementation is unchanged — it's the dispatch and serialization layers that differ.

```
                    ┌─────────────────────────────┐
                    │     ExporterService         │
Legacy client ────> │  DriverCall(uuid, method)   │ ──┐
                    └─────────────────────────────┘   │
                                                      ├──> driver.on()
                    ┌─────────────────────────────┐   │
                    │     PowerInterface          │   │
Native client ────> │  On(Empty) + UUID metadata  │ ──┘
                    └─────────────────────────────┘
```

#### Migration phases

1. **This JEP:** Generate `FileDescriptorProto` and `.proto` files. Wire protocol unchanged. Polyglot clients can use `DynamicMessage` with `DriverCall` and the descriptor.
2. **Future JEP — dual path:** Exporter registers native gRPC services alongside `DriverCall`. Compile `.proto` files to stubs. New clients choose native path. Old clients unchanged.
To be decided based on the discoveries during implementation of this JEP and the dual-path JEP
3. **Deprecation:** Mark `DriverCall` as deprecated. Migration guide published.
4. **Removal:** Remove `DriverCall` in a major version bump. All clients use native gRPC.

## Implementation Phases

| Phase | Deliverable                                                                                                           | Depends On    |
| ----- | --------------------------------------------------------------------------------------------------------------------- | ------------- |
| 1a    | `DriverInterfaceMeta` + `DriverInterface` base class — type-safe interface marking with registry and validation       | —             |
| 1b    | Migrate standard in-tree interfaces to `DriverInterface` and dual-inheritance clients (type annotations recommended)  | Phase 1a      |
| 2     | Opt-in `@export` annotation validation — warn by default, `@export(strict=True)` / `JMP_EXPORT_STRICT=1`              | —             |
| 3     | Type mapping module — Python types to protobuf field types, handling BaseModel, list, enum, UUID                      | —             |
| 4     | `build_file_descriptor()` library function for build-time use                                                         | Phase 1a, 3   |
| 5     | `jumpstarter/annotations/annotations.proto` — `resource_handle` field option                                          | —             |
| 6     | Doc comment extraction — docstrings to proto comments in builder                                                      | Phase 4       |
| 7     | Codegen CLI — Python → `.proto` source files                                                                          | Phase 4, 5, 6 |
| 8     | Commit `.proto` files and `protoc --descriptor_set_out` artifacts for standard in-tree interfaces                     | Phase 7       |
| 9     | `DriverInstanceReport.file_descriptor_proto` populated from bundled descriptor set at exporter startup                | Phase 8       |
| 10    | gRPC Server Reflection registration from bundled descriptor set (advisory; services return `UNIMPLEMENTED` if called) | Phase 8       |
| 11    | Interface check CLI — CI drift detection between committed `.proto` and live Python interface                         | Phase 7       |

Phases 1a–1b establish the type-safe interface foundation and the dual-inheritance client convention. Phase 2 delivers opt-in annotation validation. Phases 3–4 build the build-time introspection core. Phases 5–7 deliver the developer-facing tooling. Phases 8–10 deliver runtime schema exposure from the committed artifacts. Phase 11 closes the loop with CI drift detection.

Proto-first codegen and native gRPC transport are **out of scope** for this JEP and are planned as follow-up JEPs.

## Implementation History

- 2026-04-06: JEP drafted
- 2026-04-07: JEP refined — added `DriverInterface` metaclass, type enforcement on `@export`, resource handle pattern, native gRPC migration sketch; fixed Pydantic BaseModel usage, NetworkInterface proto, driver adapter scope; expanded type mapping table and unresolved questions
- 2026-04-30: Simplified — pivoted to build-time generation of committed `.proto` files, dropped proto-first adapter and dynamic runtime introspection, made type enforcement opt-in, added grpcurl `UNIMPLEMENTED` note
- 2026-05-09: Deferred concrete CLI command names (now referred to as the codegen CLI and the interface check CLI); fixed spelling typos flagged by `typos`; added out-of-tree drivers section with no-proto fallback behavior; clarified interface check CLI discovery via `DriverInterfaceMeta._registry`; expanded Pattern 4 to recommend promoting most client-side composites to server-side `@export` methods, keeping client-side orchestration only for complex drivers like network and flasher

## References

- [Protobuf Custom Options](https://protobuf.dev/programming-guides/proto3/#customoptions)
- [gRPC Server Reflection Protocol](https://github.com/grpc/grpc/blob/master/doc/server-reflection.md)
- [google.protobuf.FileDescriptorProto](https://github.com/protocolbuffers/protobuf/blob/main/src/google/protobuf/descriptor.proto)
- [Buf Schema Registry](https://buf.build/docs/bsr/introduction)
- [grpcurl](https://github.com/fullstorydev/grpcurl)
- [Jumpstarter Driver Architecture](https://docs.jumpstarter.dev/introduction/key-concepts.html)
- [Jumpstarter `@export` Decorator Source](https://github.com/jumpstarter-dev/jumpstarter/blob/main/packages/jumpstarter/jumpstarter/driver/decorators.py)
- [Python `inspect.signature()`](https://docs.python.org/3/library/inspect.html#inspect.signature)

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
