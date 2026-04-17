# JEP-0011: Protobuf Introspection and Interface Generation

| Field             | Value                                                 |
| ----------------- | ----------------------------------------------------- |
| **JEP**           | 0011                                                  |
| **Title**         | Protobuf Introspection and Interface Generation       |
| **Author(s)**     | @kirkbrauer (Kirk Brauer)                             |
| **Status**        | Draft                                                 |
| **Type**          | Standards Track                                       |
| **Created**       | 2026-04-06                                            |
| **Updated**       | 2026-04-07                                            |
| **Discussion**    | [Matrix](https://matrix.to/#/#jumpstarter:matrix.org) |
| **Requires**      | —                                                     |
| **Supersedes**    | —                                                     |
| **Superseded-By** | —                                                     |

---

## Abstract

This JEP extends the `@export` decorator to capture method signatures, builds `google.protobuf.FileDescriptorProto` descriptors from Python interface classes at runtime, and serves them through both gRPC Server Reflection and the `DriverInstanceReport` protocol. It introduces bidirectional tooling: `jmp interface generate` produces canonical `.proto` source files from Python interfaces, and `jmp interface implement` auto-generates complete, ready-to-use driver clients and driver adapters from `.proto` files — eliminating all manual dispatch plumbing so driver developers write only hardware logic. The `FileDescriptorProto` — protobuf's standard self-description format — becomes the single schema artifact consumed by gRPC reflection, typed client generation, compatibility checking, and the interface registry.

This JEP is the foundation of the Jumpstarter polyglot driver ecosystem. All subsequent JEPs in this series (DeviceClass, Polyglot Codegen, Driver Registry) build on the introspection and proto generation capability introduced here.

## Motivation

Today, the `DriverInstanceReport` returned by `GetReport` contains driver UUIDs, labels, parent-child relationships, and human-readable `methods_description` text. It does not include machine-readable method signatures — parameter names, types, return types, or call semantics (unary vs. streaming). This means non-Python clients cannot discover the shape of a driver's API without out-of-band knowledge, limiting Jumpstarter to a single-language ecosystem.

The `@export` decorator already has access to the full method signature via `inspect.signature()`, and the interface classes already carry type annotations. However, none of this information is surfaced in a structured, interoperable format. A JVM-based test runner, a TypeScript MCP server, or a Rust flash utility all have to reverse-engineer method names, argument types, and streaming semantics from Python source code or informal documentation.

Additionally, teams that want to define interface contracts upfront — before writing any driver implementation — currently have no supported workflow. A proto-first path would let architects define the interface as a `.proto` file and generate the Python scaffolding from it, following the standard gRPC development pattern while remaining fully compatible with Jumpstarter's existing driver model.

This JEP addresses three concrete gaps:

1. **Runtime introspection** — non-Python clients have no way to discover driver APIs programmatically.
2. **Schema portability** — there is no language-neutral description of Jumpstarter driver interfaces that standard protobuf/gRPC tooling can consume.
3. **Contract-first development** — teams cannot define an interface specification before (or independently of) the Python driver implementation.
4. **Manual plumbing** — driver authors hand-write three tightly-coupled classes (interface, client, driver adapter) that must agree on method names, types, and streaming semantics. Mismatches produce hard-to-debug runtime errors.

### User Stories

- **As a** Python driver developer, **I want** the `@export` decorator to enforce complete type annotations on all parameters and return types at import time, **so that** type mismatches between the interface contract, driver implementation, and client are caught immediately — not at runtime when a test fails with an inscrutable serialization error.

- **As a** Java test engineer writing Android device tests, **I want to** discover all available methods on a leased device's power driver — including parameter types, return types, and streaming semantics — **so that** I can generate type-safe Kotlin stubs instead of hand-writing `DriverCall` invocations with magic string method names.

- **As a** platform architect responsible for interface contracts across multiple driver teams, **I want to** define an interface as a `.proto` file and generate the complete Python interface, client, and driver adapter from it, **so that** the proto is the canonical source of truth and I don't have to manually wire `@export` decorators or `DriverClient` calls.

- **As a** driver developer adding a new power relay driver, **I want to** subclass a generated driver adapter that already has all the `@export` decorators and dispatch plumbing, **so that** I only write the hardware-specific logic and never touch serialization, method dispatch, or client code.

- **As a** tools developer building a device management dashboard, **I want to** point standard gRPC tooling (`grpcurl`, Postman, Buf Studio) at an exporter and discover every available driver interface with full type information, **so that** I can prototype interactions without reading Python source code.

- **As a** CI pipeline author, **I want to** run a compatibility check in CI that verifies the Python driver interface hasn't drifted from the published `.proto` definition, **so that** cross-language clients don't silently break when a driver evolves.

## Proposal

### Overview

This proposal adds three capabilities to Jumpstarter, all unified around protobuf's standard `FileDescriptorProto` format:

1. **Enhanced `@export` introspection** — the `@export` decorator captures Python type annotations and stores them as structured metadata on the decorated function.
2. **`FileDescriptorProto` builder** — a module constructs `google.protobuf.descriptor_pb2.FileDescriptorProto` objects from Python interface classes by reading the metadata stored by the `@export` decorator.
3. **Bidirectional CLI tooling** — `jmp interface generate` (Python → `.proto`), `jmp interface implement` (`.proto` → complete Python client + driver adapter + interface), and `jmp interface check` (verify bidirectional consistency).

The key design goal of `jmp interface implement` is **full auto-generation**: both the client class (used by test code) and the driver adapter class (used by driver implementations) are generated entirely from the proto definition with no manual code. The driver developer subclasses the generated adapter and writes only hardware logic.

The `FileDescriptorProto` is the connective tissue: it's the same format gRPC Server Reflection serves, the same format `buf` and `protoc` understand, and the same format that every language's protobuf library can parse to construct `DynamicMessage` instances and invoke RPCs without pre-compiled stubs.

### Wire Protocol: `DriverCall` Remains Unchanged

An important design constraint: **this JEP does not change the wire protocol.** The existing `DriverCall` and `StreamingDriverCall` RPCs — where the client sends a method name as a string and arguments as `google.protobuf.Value` — remain the actual transport mechanism. The auto-generated client code still calls `self.call("on")` and `self.streamingcall("read")` under the hood. The auto-generated driver adapter still receives dispatch through the existing `@export` decorator and `Driver` base class machinery.

The `.proto` files and `FileDescriptorProto` descriptors serve as a **description layer** on top of the existing dispatch mechanism — they describe what methods exist, what types they use, and what streaming semantics they have. They do not replace `DriverCall` with actual protobuf-native gRPC service implementations (where `PowerInterface` would be a real gRPC service with compiled request/response message stubs). That migration would be a significant breaking change to the exporter protocol, affecting every existing client and driver, and is explicitly out of scope for this JEP.

In concrete terms:

- **What the proto IS used for:** introspection (`GetReport`, gRPC reflection), code generation (typed clients and driver adapters), compatibility checking (`jmp interface check`, `buf breaking`), documentation, and the interface registry.
- **What the proto is NOT used for:** actual RPC transport. The `DriverCall(uuid="...", method="on", args=[])` message continues to be the wire format.

A future JEP may propose migrating to native protobuf service implementations — where `protoc`-generated stubs handle serialization directly and `DriverCall` is retired — but that is a separate, breaking change with its own migration path and backward compatibility analysis.

### `FileDescriptorProto` as the Schema Format

Rather than defining a custom schema message, this proposal uses protobuf's own self-description mechanism: `google.protobuf.FileDescriptorProto`. This is the same format that gRPC Server Reflection serves, that `buf` understands natively, and that every language's protobuf library can parse.

A `FileDescriptorProto` fully describes a `.proto` file in binary form: its package name, message definitions (with field names, types, and numbers), service definitions (with method names, request/response types, and streaming semantics), and import dependencies. This is strictly more expressive than any custom schema format.

Using it means there is one descriptor format throughout the entire system — generation, runtime introspection, registry, and codegen all consume the same artifact.

### Enhanced `@export` Introspection

The `@export` decorator in `jumpstarter/driver/decorators.py` is modified to capture the method signature at decoration time and store it as metadata on the function object:

```python
def export(func):
    sig = inspect.signature(func)
    type_info = ExportedMethodInfo(
        name=func.__name__,
        call_type=_infer_call_type(func),  # UNARY, SERVER_STREAMING, or BIDI_STREAMING
        params=[
            (p.name, p.annotation, p.default)
            for p in sig.parameters.values()
            if p.name != 'self'
        ],
        return_type=sig.return_annotation
    )
    setattr(func, MARKER_TYPE_INFO, type_info)
    # existing marker logic unchanged
    ...
```

The Python type annotations are stored as-is on the function object. The conversion to protobuf descriptors happens later, at `FileDescriptorProto` generation time. The `_infer_call_type()` helper examines both the parameter and return annotations to determine streaming semantics: `AsyncGenerator[T]` or `Generator[T]` as a return type indicates server streaming, an `AsyncGenerator` parameter indicates client streaming, and the combination indicates bidirectional streaming (as used by the TCP driver). All other signatures indicate unary calls. Methods decorated with `@exportstream` (detected via the `MARKER_STREAMCALL` attribute) are handled separately — they are raw byte stream constructors that use a `StreamData { bytes payload }` message for native gRPC bidi streaming (see "Driver Patterns and Introspection Scope" in Design Details).

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

### Building `FileDescriptorProto` from Interface Classes

A builder module constructs `google.protobuf.descriptor_pb2.FileDescriptorProto` programmatically from interface class introspection:

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
        type_info = getattr(method, MARKER_TYPE_INFO)

        # Build request/response message descriptors
        request_msg = _build_request_message(fd, name, type_info.params)
        response_msg = _build_response_message(fd, name, type_info.return_type)

        service.method.append(MethodDescriptorProto(
            name=_to_pascal_case(name),
            input_type=f".{package}.{request_msg.name}",
            output_type=f".{package}.{response_msg.name}",
            server_streaming=(type_info.call_type in (
                CallType.SERVER_STREAMING, CallType.BIDI_STREAMING)),
            client_streaming=(type_info.call_type == CallType.BIDI_STREAMING),
        ))

    fd.service.append(service)
    return fd
```

This produces the same `FileDescriptorProto` that `protoc` would generate from a hand-written `.proto` file.

### Custom Options and Doc Comments

Protobuf service and message definitions carry structure — method names, parameter types, streaming semantics — but out of the box they don't carry versioning metadata. Additionally, while the type mapping captures *what* a method does structurally, it doesn't capture *why* or *how* in human terms. This section addresses both gaps: a lightweight custom option for interface versioning, and systematic generation of proto comments from Python docstrings.

#### Interface Versioning

Interface versioning follows standard protobuf package-level versioning conventions. The version is encoded in the package name (e.g., `jumpstarter.interfaces.power.v1`) and the `--version` flag on `jmp interface generate`. Breaking changes to an interface require a new package version (`v1` → `v2`), and `buf breaking` enforces backward compatibility within a version.

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

The `build_file_descriptor()` builder and `jmp interface generate` tool extract docstrings from Python and emit them as proto comments:

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

When `jmp interface generate` processes the class above, the resulting `.proto` file carries the version option and doc comments:

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

The `jmp interface check` tool validates doc comments bidirectionally:

- **Python → Proto:** Verifies that docstrings in the Python source appear as proto comments in the generated `.proto` file.
- **Proto → Python:** Verifies that proto comments in a hand-authored `.proto` file produce corresponding docstrings in the generated Python code.

This ensures documentation doesn't drift regardless of which direction the developer is working from.

### The `jmp interface generate` Tool (Python → Proto)

A CLI command introspects a Python interface class and produces a canonical `.proto` source file:

```bash
jmp interface generate \
  --package jumpstarter-driver-power \
  --interface PowerInterface \
  --version v1 \
  --output python/packages/jumpstarter-driver-power/proto/power/v1/power.proto
```

The `.proto` file is co-located with the driver package that defines the interface — not in the central `protocol/` directory, which is reserved for Jumpstarter's own wire protocol (`ExporterService`, `RouterService`, etc.). This keeps interface schemas alongside their implementations and avoids confusion between the Jumpstarter protocol and driver interface contracts.

Implementation: loads the interface class via `importlib`, calls `build_file_descriptor()` to produce the `FileDescriptorProto`, then renders it as human-readable `.proto` source text. Python snake_case method names are converted to PascalCase RPC names (e.g., `read_data_by_identifier` → `rpc ReadDataByIdentifier`), following standard proto conventions. The reverse mapping is applied by `jmp interface implement`.

For batch processing of all in-tree drivers:

```bash
jmp interface generate-all
```

This walks `DriverInterfaceMeta._registry` (populated at import time) to discover all defined interfaces and generates `.proto` files into each driver package's `proto/` directory.

### The `jmp interface implement` Tool (Proto → Python)

The reverse direction: write a `.proto` file, generate all the Python artifacts needed to both use and implement the interface. The core principle is that the proto definition contains enough information to fully generate both sides of the wire — the client that test code calls and the driver adapter that handles dispatch — with zero manual plumbing.

**Naming convention: Pythonic, not protobuf.** The generated Python code follows Python naming conventions, not protobuf conventions. The `.proto` file is the schema artifact; the Python output must feel native to a Python developer:

| Proto convention                   | Generated Python                               |
| ---------------------------------- | ---------------------------------------------- |
| `rpc ReadDataByIdentifier(...)`    | `async def read_data_by_identifier(self, ...)` |
| `message PowerReading`             | `class PowerReading(BaseModel)`                |
| `double voltage = 1;`              | `voltage: float`                               |
| `repeated DidValue values = 1;`    | `values: list[DidValue]`                       |
| `optional string name = 1;`        | `name: str \| None = None`                     |
| `enum SessionType { ... }`         | `class SessionType(StrEnum)`                   |
| `google.protobuf.Empty` (request)  | no parameter                                   |
| `google.protobuf.Empty` (response) | `-> None`                                      |

Method names are converted from PascalCase (`ReadDataByIdentifier`) to snake_case (`read_data_by_identifier`). Message names remain PascalCase (Python class convention). Field names are already snake_case in proto3 convention. The generated code uses Pydantic `BaseModel` for messages, `StrEnum` for enums, and standard Python type annotations throughout — no `_pb2` imports or protobuf-generated classes appear in the generated interface, client, or driver adapter.

```bash
jmp interface implement \
  --proto python/packages/jumpstarter-driver-power/proto/power/v1/power.proto \
  --output-package jumpstarter_driver_power \
  --output src/jumpstarter_driver_power/
```

From a proto like:

```protobuf
syntax = "proto3";
package jumpstarter.interfaces.power.v1;

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

The tool generates four files:

#### Generated: Interface class (`interface.py`)

The abstract contract that defines what the interface looks like in Python. This is the type that both drivers and clients are coded against:

```python
# generated: jumpstarter_driver_power/interface.py
from abc import abstractmethod
from collections.abc import AsyncGenerator
from pydantic import BaseModel
from jumpstarter.driver import DriverInterface

class PowerReading(BaseModel):
    voltage: float
    current: float

class PowerInterface(DriverInterface):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_power.client.PowerClient"

    @abstractmethod
    async def on(self) -> None: ...

    @abstractmethod
    async def off(self) -> None: ...

    @abstractmethod
    async def read(self) -> AsyncGenerator[PowerReading, None]: ...
```

#### Generated: Client class (`client.py`) — fully auto-generated, no client-side logic

The client class is **complete and ready to use**. There is no client-side logic to write — every method is a mechanical translation from a typed Python call to a `DriverCall` RPC:

```python
# generated: jumpstarter_driver_power/client.py
from collections.abc import Generator
from jumpstarter.client import DriverClient
from .interface import PowerInterface, PowerReading

class PowerClient(PowerInterface, DriverClient):
    """Auto-generated client for PowerInterface.

    This class is fully generated from power.proto. Do not edit — 
    regenerate with `jmp interface implement` when the proto changes.
    """

    def on(self) -> None:
        self.call("on")

    def off(self) -> None:
        self.call("off")

    def read(self) -> Generator[PowerReading, None, None]:
        for raw in self.streamingcall("read"):
            yield PowerReading.model_validate(raw, strict=True)
```

The generated client inherits from both `PowerInterface` (ensuring type-safety against the contract) and `DriverClient` (providing the `call()` / `streamingcall()` transport). Every method body is a one-liner that delegates to the underlying `DriverCall` RPC — serializing arguments to `google.protobuf.Value`, dispatching by method name, and deserializing the response into the typed return value. Pydantic models are deserialized using `model_validate()` rather than constructor kwargs, consistent with existing Jumpstarter client conventions.

**Note:** In the current codebase, client classes inherit only from `DriverClient` (e.g., `class PowerClient(DriverClient)`). The generated pattern of inheriting from both the interface and `DriverClient` (e.g., `class PowerClient(PowerInterface, DriverClient)`) is a new convention introduced by this JEP. It provides compile-time verification that the client implements all interface methods. Existing clients are not required to change, but new auto-generated clients will follow this pattern.

Test code uses the client directly with no wiring:

```python
async with client.lease("android-headunit") as headunit:
    power: PowerClient = headunit.power  # typed, auto-complete works
    power.on()                           # typed call, no magic strings
    for reading in power.read():
        assert reading.voltage > 4.5     # typed field access
```

#### Generated: Driver adapter (`driver.py`) — for proto-first development

The driver adapter is generated by `jmp interface implement` for the **proto-first workflow** — when a team defines the interface as a `.proto` file before writing the Python implementation. It provides `@export`-decorated methods with dispatch plumbing so the driver developer writes only hardware logic.

**This adapter is not required for Python-first development.** Existing drivers that put `@export` directly on their methods (the current standard pattern) continue to work unchanged. The adapter is an opt-in convenience for proto-first teams.

The adapter is the server-side counterpart to the client. It provides the `@export`-decorated methods that the Jumpstarter exporter framework requires for `DriverCall` dispatch, and it delegates every call to the corresponding abstract method from the interface. Like the client, it contains **no business logic** — it's a mechanical translation from the proto definition:

```python
# generated: jumpstarter_driver_power/driver.py
from collections.abc import AsyncGenerator
from jumpstarter.driver import Driver, export
from .interface import PowerInterface, PowerReading

class PowerDriver(PowerInterface, Driver):
    """Auto-generated driver adapter for PowerInterface.

    Subclass this and implement the abstract methods with your
    hardware-specific logic. The @export decorators, type annotations,
    and streaming semantics are handled by this generated adapter.

    Do not edit — regenerate with `jmp interface implement` when the
    proto changes.
    """

    @export
    async def on(self) -> None:
        return await self._on()

    @export
    async def off(self) -> None:
        return await self._off()

    @export
    async def read(self) -> AsyncGenerator[PowerReading, None]:
        async for reading in self._read():
            yield reading

    # ── Abstract methods for driver implementors ──────────────

    @abstractmethod
    async def _on(self) -> None: ...

    @abstractmethod
    async def _off(self) -> None: ...

    @abstractmethod
    async def _read(self) -> AsyncGenerator[PowerReading, None]: ...
```

The adapter pattern separates the Jumpstarter dispatch plumbing (the `@export`-decorated public methods) from the driver implementation (the abstract `_methods`). The `@export` decorators, type annotations, streaming wrappers, and serialization hints are all generated from the proto — the driver developer never touches them.

#### What the driver developer writes

The driver developer subclasses the generated adapter and implements only the abstract methods — the actual hardware interaction:

```python
# user-written: jumpstarter_driver_power/drivers/yepkit.py
from ..driver import PowerDriver
from ..interface import PowerReading

class Ykush(PowerDriver):
    """Yepkit YKUSH USB power switching hub."""
    serial: str
    port: str

    async def _on(self) -> None:
        self._usb_control("on", self.port)

    async def _off(self) -> None:
        self._usb_control("off", self.port)

    async def _read(self):
        yield PowerReading(voltage=5.0, current=self._read_current())
```

The driver developer's file contains **only hardware logic**. No `@export` decorators, no `DriverClient` wiring, no `call()` / `streamingcall()` plumbing, no serialization code. The generated adapter and client handle all of that.

**Alternative: Python-first workflow (existing pattern)**

Drivers developed Python-first continue to implement `@export` methods directly, without the adapter indirection. The `jmp interface generate` tool can produce a `.proto` from these drivers for cross-language consumption, but no adapter class is involved:

```python
# Python-first: driver puts @export directly on implementation methods
class Ykush(PowerInterface, Driver):
    serial: str
    port: str

    @export
    async def on(self) -> None:
        self._usb_control("on", self.port)

    @export
    async def off(self) -> None:
        self._usb_control("off", self.port)

    @export
    async def read(self) -> AsyncGenerator[PowerReading, None]:
        yield PowerReading(voltage=5.0, current=self._read_current())
```

Both workflows produce the same `FileDescriptorProto` and the same `.proto` file — the difference is only in how the Python code is organized. The proto-first adapter pattern separates dispatch plumbing from hardware logic; the Python-first pattern combines them in one class.

#### Why full auto-generation matters

Today, adding a new Jumpstarter driver interface requires writing three tightly-coupled classes by hand: the interface, the client, and the driver base with `@export` decorators. All three must agree on method names, argument types, streaming semantics, and serialization conventions. Any mismatch produces runtime errors that are difficult to debug — a typo in a `self.call("on")` string, a missing `@export` decorator, or a mismatched return type.

With proto-driven auto-generation, the developer defines the contract once (either as a Python interface or a `.proto` file) and the tooling produces both sides of the wire. The generated code is correct by construction — the client's `self.call("on")` always matches the driver's `@export async def on()` because they're generated from the same proto definition. This eliminates an entire class of integration bugs.

For a new interface, the workflow becomes:

1. Define the interface (Python-first or proto-first)
2. Run `jmp interface implement` (or `jmp interface generate` + `jmp interface implement`)
3. Subclass the generated `PowerDriver` with hardware logic
4. Ship it — the client, dispatch adapter, type annotations, and streaming plumbing are all generated

**Scope of full auto-generation:** The complete auto-generation workflow — where both client and driver adapter are generated with no manual code — applies to interfaces whose methods use simple, typed parameters and return values (e.g., `PowerInterface`, basic `NetworkInterface`). These represent the majority of Jumpstarter driver interfaces.

Interfaces that use resource handles (`FlasherInterface`, `StorageMuxInterface`), complex client-side orchestration, or domain-specific adapters require hand-written client logic layered on top of the generated base. The generated client provides the typed dispatch foundation; the hand-written extension adds the orchestration. See "Resource Handle Pattern" in Design Details.

#### Generated: `__init__.py` — public API surface

The tool also generates an `__init__.py` that exports the public API:

```python
# generated: jumpstarter_driver_power/__init__.py
from .interface import PowerInterface, PowerReading
from .client import PowerClient
from .driver import PowerDriver

__all__ = [
    "PowerInterface",
    "PowerReading",
    "PowerClient",
    "PowerDriver",
]
```

### The `jmp interface check` Tool (Consistency Verification)

When both directions exist, the tooling can verify they agree:

```bash
jmp interface check \
  --proto python/packages/jumpstarter-driver-power/proto/power/v1/power.proto \
  --interface jumpstarter_driver_power.interface.PowerInterface
```

This compares the `FileDescriptorProto` built from the Python class against the one parsed from the `.proto` file and reports mismatches — missing methods, type differences, streaming semantics changes. This can run in CI alongside `buf breaking` to catch drift between the proto and the Python implementation.

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

The field is `optional bytes` (not a nested message) because `FileDescriptorProto` is a well-known protobuf type that clients parse with their own language's descriptor library. Keeping it as raw bytes avoids adding `google/protobuf/descriptor.proto` as a direct dependency of the Jumpstarter protocol.

**This change is additive.** Old clients ignore the new field. Old exporters do not populate it.

#### gRPC Server Reflection

At exporter startup, the `Session` registers the same `FileDescriptorProto` objects with `grpcio-reflection`:

```python
from grpc_reflection.v1alpha import reflection

def register_reflection(server, root_device):
    service_names = [reflection.SERVICE_NAME]
    descriptors = []

    for uuid, interface_class, labels, instance in root_device.enumerate():
        fd = build_file_descriptor(interface_class)
        descriptors.append(fd)
        service_names.append(fd.service[0].name)

    reflection.enable_server_reflection(service_names, server)
```

This serves the descriptors through the standard `grpc.reflection.v1.ServerReflection` service, enabling standard tools (`grpcurl`, Postman, Java's `ProtoReflectionDescriptorDatabase`) to discover and interact with every driver interface on any exporter.

The `file_descriptor_proto` in the report and the gRPC reflection service serve the same data through different channels. The report embeds the descriptor for clients that want it inline with the driver tree. Reflection serves it through the standard gRPC mechanism for tools that expect that protocol. They are the same `FileDescriptorProto` — no duplication of schema definitions.

### Hardware Considerations

This JEP is a purely software-layer change. No hardware is required or affected. The introspection operates on Python type annotations and produces protobuf descriptors; it does not interact with physical devices, USB interfaces, or timing-sensitive operations. The `FileDescriptorProto` is generated at import time (for the `@export` decorator metadata) and at startup time (for reflection registration and report population), introducing negligible overhead.

Exporters running on resource-constrained SBCs (e.g., Raspberry Pi 4) should see no measurable performance impact. The `FileDescriptorProto` for a typical driver interface with 5–10 methods is approximately 1–3 KB serialized.

## Design Details

### Architecture

```
┌────────────────────────────┐
│   Python Interface Class   │  (PowerInterface, AdbInterface, etc.)
│   with @export methods     │
└─────────────┬──────────────┘
              │  inspect.signature() + type annotations
              ▼
┌────────────────────────────┐
│  ExportedMethodInfo        │  Stored as metadata on function objects
│  (name, call_type, params, │  via MARKER_TYPE_INFO attribute
│   return_type)             │
└─────────────┬──────────────┘
              │  build_file_descriptor()
              ▼
┌────────────────────────────┐
│  FileDescriptorProto       │  The universal schema artifact
│  (binary protobuf)         │
└──┬──────────┬──────────┬───┘
   │          │          │
   ▼          ▼          ▼
┌──────┐  ┌───────┐  ┌──────────────────┐
│ gRPC │  │Report │  │.proto source     │
│Reflec│  │(bytes)│  │(jmp interface    │
│tion  │  │       │  │ generate)        │
└──────┘  └───────┘  └──────────────────┘
```

For the proto-first direction:

```
┌────────────────────────────┐
│  .proto source file        │  Hand-written or from external contract
└─────────────┬──────────────┘
              │  protoc parse → FileDescriptorProto
              ▼
┌────────────────────────────┐
│  jmp interface implement   │  Jumpstarter code generator
└──┬──────────┬──────────┬───┘
   │          │          │
   ▼          ▼          ▼
┌──────┐  ┌───────┐  ┌──────┐
│inter-│  │driver │  │client│
│face  │  │_base  │  │.py   │
│.py   │  │.py    │  │      │
└──────┘  └───────┘  └──────┘
```

### Data Flow

1. **At import time:** The `@export` decorator fires, captures `inspect.signature()`, creates an `ExportedMethodInfo`, and stores it as an attribute on the function object. This adds ~0.1 ms per decorated method.

2. **At exporter startup:** The `Session` calls `build_file_descriptor()` for each interface class in the driver tree, producing `FileDescriptorProto` objects. These are:
   - Registered with `grpc_reflection` for standard gRPC reflection.
   - Serialized to bytes and embedded in each `DriverInstanceReport`.

3. **At `GetReport` time:** The client receives the report with embedded `file_descriptor_proto` bytes. It can parse them with its language's protobuf library to discover the full interface schema.

4. **At codegen time:** The `jmp interface generate` command loads an interface class, calls `build_file_descriptor()`, and renders the `FileDescriptorProto` as human-readable `.proto` source text. The `jmp interface implement` command does the reverse.

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
    - Interface registry for jmp interface generate-all
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

| Interface | Package | Current State | Notes |
|-----------|---------|---------------|-------|
| `PowerInterface` | `jumpstarter-driver-power` | ABCMeta, fully typed | Straightforward migration |
| `VirtualPowerInterface` | `jumpstarter-driver-power` | ABCMeta, fully typed | Separate from PowerInterface; `off(destroy: bool = False)` differs |
| `NetworkInterface` | `jumpstarter-driver-network` | ABCMeta | `connect()` missing return type annotation |
| `FlasherInterface` | `jumpstarter-driver-opendal` | ABCMeta | `flash(source)` and `dump(target)` missing param types |
| `StorageMuxInterface` | `jumpstarter-driver-opendal` | ABCMeta | 5 methods missing return types |
| `StorageMuxFlasherInterface` | `jumpstarter-driver-opendal` | Inherits StorageMuxInterface | No own methods; just overrides `client()` |
| `CompositeInterface` | `jumpstarter-driver-composite` | **No metaclass (plain class)** | Empty interface, no abstract methods |

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
- `jmp interface generate-all` iterates the registry — no package entry-point scanning needed

**Migration:** Each interface changes from `metaclass=ABCMeta` to inheriting `DriverInterface`. Drivers that inherit from both the interface and `Driver` continue to work since `DriverInterfaceMeta` extends `ABCMeta`. The migration also requires adding full type annotations to all abstract methods — this is the forcing function for making the entire interface ecosystem type-safe.

### Type Enforcement on the `@export` Decorator

The `@export` decorator is enhanced to validate type annotations at decoration time. This completes the type safety chain: `DriverInterface` enforces annotations on the contract, and `@export` enforces annotations on the implementation.

```python
def export(func):
    """Decorator for exporting method as driver call.

    Validates that the method has complete type annotations
    for all parameters and the return type.
    """
    sig = inspect.signature(func)

    # Validate return type annotation exists
    if sig.return_annotation is inspect.Parameter.empty:
        raise TypeError(
            f"@export method {func.__qualname__} must have a return type annotation. "
            f"Use '-> None' for methods that return nothing."
        )

    # Validate all parameters (except self) have type annotations
    for param in sig.parameters.values():
        if param.name == "self":
            continue
        if param.annotation is inspect.Parameter.empty:
            raise TypeError(
                f"@export method {func.__qualname__}: parameter '{param.name}' "
                f"must have a type annotation."
            )

    # Store type info for introspection (ExportedMethodInfo)
    type_info = ExportedMethodInfo(
        name=func.__name__,
        call_type=_infer_call_type(func),
        params=[
            (p.name, p.annotation, p.default)
            for p in sig.parameters.values()
            if p.name != "self"
        ],
        return_type=sig.return_annotation,
    )
    setattr(func, MARKER_TYPE_INFO, type_info)

    # Existing marker logic (unchanged)
    if isasyncgenfunction(func) or isgeneratorfunction(func):
        setattr(func, MARKER_STREAMING_DRIVERCALL, MARKER_MAGIC)
    elif iscoroutinefunction(func) or isfunction(func):
        setattr(func, MARKER_DRIVERCALL, MARKER_MAGIC)
    else:
        raise ValueError(f"unsupported exported function {func}")

    return func
```

The same validation applies to `@exportstream`. This means:

- `@export async def on(self) -> None:` — passes validation
- `@export async def on(self):` — `TypeError`: missing return type
- `@export async def flash(self, source):` — `TypeError`: `source` missing annotation
- `@export async def flash(self, source: str) -> None:` — passes

**Impact on existing drivers:** Any driver with `@export` or `@exportstream` methods missing annotations will fail at import time. This is intentional — it forces the codebase to be fully typed before introspection can work. A codebase audit identified **~111 methods** across **25 driver packages** that need annotation fixes:

**Interface-level gaps (abstract methods):**

- `FlasherInterface.flash(source)` — `source` needs annotation (resource handle, type as `str`)
- `FlasherInterface.dump(target)` — `target` needs annotation
- `StorageMuxInterface.host()`, `.dut()`, `.off()` — need `-> None` return types
- `StorageMuxInterface.write(src)`, `.read(dst)` — need return type annotations
- `NetworkInterface.connect()` — needs return type annotation

**Driver implementation gaps (~99 `@export` methods):**

The majority (~90%) are missing `-> None` return types on void methods — a mechanical fix. ~10 methods also have missing parameter types, primarily `source` and `target` parameters on flasher implementations (resource handle UUIDs). Affected packages include: dutlink, energenie, esp32, flashers, gpiod, http, http-power, iscsi, network, noyito-relay, opendal, pi-pico, probe-rs, pyserial, qemu, ridesx, sdwire, shell, snmp, ssh, tasmota, tftp, tmt, yepkit, and composite (test file).

**`@exportstream` methods (all 12 missing return types):**

Every production `@exportstream` method uses `@asynccontextmanager` and lacks a return type annotation. These are all `connect()` methods across: ble, network (7 classes), pyserial (2), ssh-mitm, and ustreamer. The appropriate return type for `@exportstream @asynccontextmanager` methods is `-> AsyncIterator[None]` (the unwrapped generator signature before `@asynccontextmanager` wraps it).

**Migration strategy:** Interface changes and annotation fixes on all implementing drivers must happen simultaneously to avoid import-time failures. See the Phase 1b migration checklist for the complete work order.

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

Codegen tools infer the dispatch mechanism from the proto structure: a bidirectional streaming RPC with `StreamData` request and response is a raw byte stream constructor (`@exportstream`). The `StreamData` pattern is unambiguous — no custom annotation is needed. The generated native gRPC servicer bridges bytes between the gRPC stream and the driver's `@exportstream` context manager:

```python
# Auto-generated client
class NetworkClient(NetworkInterface, DriverClient):
    def connect(self):
        """Opens a raw byte stream. Use as: with client.stream("connect") as s: ..."""
        return self.stream("connect")
```

```python
# Auto-generated driver adapter (proto-first workflow only)
class NetworkDriver(NetworkInterface, Driver):
    @exportstream
    @asynccontextmanager
    async def connect(self):
        async with self._connect() as stream:
            yield stream

    @abstractmethod
    @asynccontextmanager
    async def _connect(self): ...
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

Some client classes add methods that aren't in the interface contract:

```python
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

`cycle()` is a convenience method that composes `off()` + `sleep()` + `on()` — it doesn't correspond to an `@export` method on the driver. Similarly, `StorageMuxFlasherClient` has `flash()` and `dump()` methods that orchestrate multiple interface calls with `OpendalAdapter` logic.

These methods are **not represented in the proto** because they don't cross the wire — they're client-side compositions of methods that do. The auto-generated client includes only the interface methods (the wire-crossing ones). Convenience methods like `cycle()` are added by hand in a subclass of the generated client, or provided as utility functions alongside the generated code:

```python
# Auto-generated (from proto):
class PowerClient(PowerInterface, DriverClient):
    def on(self) -> None: self.call("on")
    def off(self) -> None: self.call("off")
    def read(self) -> Generator[PowerReading, None, None]: ...

# Hand-written extension (by driver author):
class ExtendedPowerClient(PowerClient):
    def cycle(self, wait: int = 2):
        self.off()
        time.sleep(wait)
        self.on()
```

This is an explicit design choice: the generated client is a clean, mechanical translation of the interface contract. Client-side orchestration logic is layered on top, not mixed in.

#### Pattern 5: Resource handle methods

Some interfaces use resource handles — opaque identifiers representing client-side streams negotiated through the Jumpstarter resource system. The `FlasherInterface` and `StorageMuxInterface` are the primary examples:

```python
class FlasherInterface(DriverInterface):
    @abstractmethod
    def flash(self, source: str, target: str | None = None) -> None: ...
```

On the driver side, `source` is a resource UUID received via `DriverCall`. On the client side, the actual `flash()` method creates an `OpendalAdapter` context manager, negotiates a stream handle, and passes it to `self.call("flash", handle, target)`. This orchestration involves file hashing, compression negotiation, and operator selection — none of which can be expressed in protobuf.

On the wire, resource handles are UUIDs (strings) — they are passed as `string` parameters through `DriverCall`. The generated `.proto` represents these as `string` with a custom annotation `jumpstarter.annotations.resource_handle = true` on the field, signaling to codegen tools that this parameter is a resource reference, not a plain string.

The auto-generated client will produce a simple `self.call("flash", source, target)` stub that passes the resource UUID directly. However, the hand-written `FlasherClient` with its `OpendalAdapter` orchestration (file hashing, compression negotiation, stream setup) remains necessary for the full Python client experience. Teams can subclass the generated client to add this orchestration. For polyglot clients (Java, Kotlin), the resource handle protocol would need a separate specification (likely a follow-up JEP) describing how to create and manage resource streams from non-Python languages.

This pattern affects: `FlasherInterface`, `StorageMuxInterface`, `StorageMuxFlasherInterface`, and the OpenDAL storage driver.

### Error Handling and Failure Modes

- **Missing type annotations:** The `@export` decorator enforces that all parameters (except `self`) and the return type have type annotations. Missing annotations raise `TypeError` at import time, preventing drivers with incomplete type information from loading. This is a deliberate enforcement mechanism — see "Type Enforcement on the `@export` Decorator" above.

- **Unsupported types:** Complex Python types that don't have a clean protobuf mapping (e.g., `Union[str, int]`, custom metaclasses) produce a warning and fall back to `google.protobuf.Value`. A future JEP may introduce `oneof` support for `Union` types.

- **Circular references in dataclasses:** The builder detects cycles during recursive field introspection and raises a descriptive error at startup rather than entering infinite recursion.

- **Reflection registration failure:** If `grpcio-reflection` is not installed, the exporter logs a warning and continues without reflection. The `file_descriptor_proto` field in the report is still populated.

- **Proto parse failure in `jmp interface implement`:** If the input `.proto` file is malformed, `protoc` (invoked as a subprocess) produces a standard error message. The `jmp` CLI surfaces this with context about which file failed.

### Concurrency and Thread-Safety

The `ExportedMethodInfo` metadata is set once at decoration time (import) and is read-only thereafter — no locking required. The `build_file_descriptor()` function is pure (no side effects, no mutation of inputs) and safe to call from any thread. The gRPC reflection service is thread-safe by design (`grpcio-reflection` handles concurrent requests internally).

### Security Implications

gRPC Server Reflection exposes the full interface schema to any client that can reach the exporter's gRPC port. In Jumpstarter's architecture, the exporter is already behind the operator's authentication and lease system — only clients with a valid lease can dial the exporter. Reflection does not bypass this; it's registered on the same `grpc.Server` that serves `ExporterService` and inherits its transport security (mTLS via cert-manager).

The `file_descriptor_proto` bytes in the report are served through the authenticated `GetReport` RPC and carry no additional security concern.

## Test Plan

### Unit Tests

- **Type mapping:** Verify each Python type in the mapping table produces the correct protobuf field type. Parameterized tests covering `str`, `int`, `float`, `bool`, `bytes`, `None`, `dict`, `Any`, `Optional[T]`, `@dataclass`, `AsyncGenerator[T]`.
- **`ExportedMethodInfo` capture:** Verify the `@export` decorator stores correct metadata for methods with various signatures (no params, multiple params, defaults, streaming returns).
- **`build_file_descriptor()` output:** Verify the produced `FileDescriptorProto` has correct package name, service name, method count, method names, input/output types, and streaming flags for representative interface classes.
- **Round-trip consistency:** Generate a `FileDescriptorProto` from a Python interface, render it as `.proto` source, parse the source back, and verify the descriptors are semantically identical.
- **Edge cases:** Missing annotations (fallback to `Value`), `Optional` fields, recursive dataclasses, empty interfaces.
- **Doc comment extraction:** Verify that class, method, and field docstrings are captured in the `FileDescriptorProto`'s `source_code_info` and rendered as proto comments by `jmp interface generate`.
- **Package versioning:** Verify that the `--version` flag produces the correct package name suffix (e.g., `jumpstarter.interfaces.power.v1` vs `v2`).
- **Doc comment round-trip:** Generate a `.proto` with comments from Python, then generate Python back from that `.proto`, and verify docstrings are preserved.
- **`@exportstream` detection:** Verify that methods decorated with `@exportstream` are detected by `build_file_descriptor()` and emitted as bidi streaming methods with `BytesValue` request/response types, distinct from `@export` methods.
- **Mixed `@export` / `@exportstream` interfaces:** Verify that an interface class containing both `@export` and `@exportstream` methods (like `TcpNetwork` with `address` + `connect`) produces a single `ServiceDescriptorProto` with correctly differentiated method types.
- **Auto-generated client for stream methods:** Verify that `jmp interface implement` generates `self.stream()` calls for bidi `BytesValue` streaming methods and `self.call()` / `self.streamingcall()` for standard methods.
- **Empty service (CompositeInterface):** Verify that an interface with no abstract methods produces a valid `ServiceDescriptorProto` with zero methods.

### Integration Tests

- **Reflection discovery:** Start an exporter with a known driver tree, connect with `grpcurl`, and verify that `grpcurl list` returns the expected service names and `grpcurl describe` returns correct method signatures.
- **Report introspection:** Lease a device, call `GetReport`, parse the `file_descriptor_proto` bytes, and verify they describe the correct interface.
- **`jmp interface generate` end-to-end:** Run the CLI against an installed driver package and verify the output `.proto` file is valid (passes `buf lint`) and matches the expected schema.
- **`jmp interface implement` end-to-end:** Generate Python code from a `.proto` file, import the generated modules, and verify: (a) the interface class has correct abstract methods, (b) the client class inherits from both the interface and `DriverClient` with correct `call()`/`streamingcall()` dispatch for every method, (c) the driver adapter has correct `@export` decorators with proper delegation to abstract `_methods`, and (d) a concrete subclass of the driver adapter can be instantiated and exercised through the client.
- **`jmp interface check` end-to-end:** Introduce a deliberate mismatch between a `.proto` file and a Python interface and verify the tool detects and reports it.
- **Doc comments in generated code:** Run `jmp interface implement` on a `.proto` file with comments and verify the generated Python contains corresponding docstrings.

### Hardware-in-the-Loop Tests

No HiL tests are required for this JEP. The introspection layer operates entirely on Python type metadata and protobuf descriptors; it does not interact with physical hardware.

### Manual Verification

- Point `grpcurl` at a running exporter with the new reflection service and verify interactive exploration works as expected.
- Use Buf Studio or Postman's gRPC support to connect to an exporter and verify the interface is browsable with full type information.
- Generate `.proto` files for several existing in-tree drivers (power, serial, storage-mux, adb) and review them for correctness and idiomatic proto style.

## Graduation Criteria

### Experimental

- All existing in-tree interface classes produce valid `FileDescriptorProto` descriptors without errors.
- `jmp interface generate` produces `.proto` files that pass `buf lint` for all in-tree interfaces.
- Generated `.proto` files include doc comments extracted from Python docstrings.
- `jmp interface implement` generates compilable, importable Python from those `.proto` files — including complete client classes and driver adapters with no manual wiring required.
- The `file_descriptor_proto` field is populated in `DriverInstanceReport` on at least one CI-connected exporter.
- At least one non-Python client (e.g., a Kotlin prototype or `grpcurl` script) successfully discovers and calls a driver method using only the generated proto schema.
- `jumpstarter/annotations.proto` is published and importable by external `.proto` files.

### Stable

- The type mapping table is finalized and documented.
- `jmp interface check` runs in CI for all in-tree drivers, catching any drift between `.proto` files and Python interfaces — including doc comment and version drift.
- At least two downstream JEPs (DeviceClass, Codegen, or Registry) have been implemented using the `FileDescriptorProto` artifacts from this JEP.
- Codegen for at least one non-Python language (Kotlin or TypeScript) produces documented client code from proto comments.
- No breaking changes to the `FileDescriptorProto` structure or `jumpstarter/annotations.proto` for at least one release cycle.

## Backward Compatibility

This JEP is **fully backward compatible.** All changes are additive:

- The `file_descriptor_proto` field (field number 6) is added to `DriverInstanceReport` as `optional bytes`. Old clients using generated stubs from the current `.proto` definition will simply ignore the unknown field — this is standard protobuf behavior. Old exporters will not populate the field, and clients must handle its absence.

- gRPC Server Reflection is a separate service (`grpc.reflection.v1.ServerReflection`) registered alongside `ExporterService`. It is invisible to clients that don't query it. No existing RPCs are modified.

- The `@export` decorator stores additional metadata on function objects via `setattr`. This does not change the decorator's existing behavior — existing markers, dispatch logic, and call semantics are untouched.

- The `jmp interface generate`, `jmp interface implement`, and `jmp interface check` commands are new CLI subcommands. They do not modify any existing commands.

- The `DriverCall` and `StreamingDriverCall` wire protocol is completely unchanged. The exporter still resolves method names as strings and serializes arguments as `google.protobuf.Value`. The auto-generated client and driver adapter code use the existing `call()` / `streamingcall()` and `@export` mechanisms — the proto descriptors describe the interface but do not replace the dispatch path. Migrating to native protobuf service implementations is explicitly out of scope (see "Wire Protocol: `DriverCall` Remains Unchanged" in the Proposal).

- The proto-first generation path is entirely opt-in. Existing Python-first drivers work without modification.

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
- The `build_file_descriptor()` approach is pure Python, runs at import time, and requires no external tooling.

### Storing type info in `methods_description` strings

Encoding type information into the existing `methods_description` map (e.g., as a JSON string per method) was considered. This was rejected because:

- It's a hack that conflates human-readable documentation with machine-readable schema.
- It doesn't integrate with any existing tooling.
- The `file_descriptor_proto` field is the proper place for machine-readable schema, and `methods_description` remains for human consumption.

## Prior Art

- **gRPC Server Reflection** ([grpc.io/docs/guides/reflection](https://grpc.io/docs/guides/reflection/)) — the standard mechanism for runtime service discovery in gRPC. This JEP uses the exact same `FileDescriptorProto` format and `ServerReflection` service definition.

- **Buf Schema Registry** ([buf.build](https://buf.build/)) — a hosted registry for protobuf schemas. Jumpstarter's `jmp interface generate` produces `.proto` files that are compatible with Buf's lint, breaking-change detection, and registry tooling.

- **Kubernetes Custom Resource Definitions (CRDs)** — Kubernetes uses OpenAPI v3 schemas embedded in CRDs for the same purpose: making API resources self-describing. Jumpstarter's approach is analogous but uses protobuf's native self-description mechanism instead of OpenAPI.

- **LAVA (Linaro Automated Validation Architecture)** — LAVA uses device type definitions and Jinja2 templates to describe hardware capabilities. Jumpstarter's approach is more strongly typed (protobuf vs. YAML templates) but serves the same goal of making device capabilities machine-discoverable.

- **Robot Framework Remote Library Interface** — Robot Framework's remote library protocol uses XML-RPC with `get_keyword_names` and `get_keyword_arguments` introspection. This JEP serves a similar purpose but uses a modern, strongly-typed, multi-language format.

## Unresolved Questions

### Must resolve before acceptance

1. **Field number assignment for `file_descriptor_proto`:** ~~Field number 6 is proposed. Need to confirm no in-flight PRs are using field 6 in `DriverInstanceReport`.~~ **Resolved:** Field 6 is already defined in `protocol/proto/jumpstarter/v1/jumpstarter.proto` as `optional bytes file_descriptor_proto = 6`.

2. **`grpcio-reflection` as required vs. optional dependency:** Should `grpcio-reflection` be a hard dependency of `jumpstarter` core, or an optional extra (`pip install jumpstarter[reflection]`)? Hard dependency is simpler; optional reduces install size for constrained environments.

3. **Proto package naming convention:** The proposed convention is `jumpstarter.interfaces.{name}.{version}` (e.g., `jumpstarter.interfaces.power.v1`). Should this be formalized as a requirement for all interfaces, or should driver authors have flexibility?

4. **`UdsInterface` refactoring:** ~~The `UdsInterface` concrete mixin pattern (where `@export` is on the interface itself) must be refactored to use `DriverInterface` + `@abstractmethod`. Should this refactoring be a prerequisite for JEP-0011, or tracked as a separate cleanup?~~ **Resolved:** Deferred to a follow-up task. `UdsInterface` is excluded from Phase 1b migration. The builder will handle non-`DriverInterface` classes via a legacy fallback path during the transition. See "Deferred: `UdsInterface` concrete mixin" in Design Details.

5. **Migration timeline for `DriverInterfaceMeta`:** ~~Should all existing interfaces migrate to the new `DriverInterface` base class in Phase 1, or can migration be gradual?~~ **Resolved:** All standard interfaces (PowerInterface, VirtualPowerInterface, NetworkInterface, FlasherInterface, StorageMuxInterface, StorageMuxFlasherInterface, CompositeInterface) migrate in Phase 1b. UdsInterface is deferred. FlasherClientInterface (a client-side ABC) is explicitly out of scope.

### Can wait until implementation

6. **`Union` type mapping:** How should `Union[str, int]` map to protobuf? `oneof` is the natural choice but adds complexity. Deferring to a future JEP is acceptable since `Union` is rarely used in current driver interfaces.

7. **Bidirectional streaming mapping:** The `@export` decorator supports `STREAM` (bidirectional) in addition to `UNARY` and `SERVER_STREAMING` — the TCP driver already uses bidirectional streaming. The proto mapping for bidirectional streaming (`stream → stream`) and the corresponding auto-generated client/driver adapter code need careful design: the client must produce a `RouterStream` or `MetadataStream` wrapper, and the driver adapter must forward the bidirectional channel correctly. This is required for completeness but can be implemented after unary and server-streaming support is stable.

8. **Proto style guide:** Should generated `.proto` files follow Google's style guide, Buf's style guide, or a Jumpstarter-specific convention? This affects field naming (snake_case vs. camelCase) and file organization.

9. **Docstring format for proto comments:** Should the builder strip reStructuredText or Google-style docstring directives (`:param:`, `Args:`, `Returns:`) before emitting proto comments, or pass them through verbatim? Stripping produces cleaner proto but loses structured parameter documentation.

10. **Driver adapter method naming:** The generated driver adapter uses underscore-prefixed abstract methods (`_on()`, `_off()`) to separate dispatch plumbing from implementation. Should this convention be `_on()`, `do_on()`, `impl_on()`, or something else? The prefix must be consistent and unlikely to collide with user-defined methods.

11. **Resource handle annotation design:** Methods like `FlasherInterface.flash(source)` take resource handles that are UUIDs on the wire but represent client-negotiated streams. The proto should type these as `string` with a `jumpstarter.annotations.resource_handle = true` field option. Should this annotation be added to `jumpstarter/annotations.proto` in Phase 1, or deferred until the resource protocol is specified for polyglot clients?

12. **Pydantic model features beyond simple fields:** Pydantic models can have validators, computed properties (`apparent_power` on `PowerReading`), model config, and custom serialization. The builder introspects `model_fields` only — validators and computed properties are not represented in the proto. Is this acceptable, or should computed properties be surfaced as read-only fields?

## Future Possibilities

The following are **not** part of this JEP but are natural extensions enabled by it:

- **DeviceClass contracts and structural enforcement:** With machine-readable interface schemas, a `DeviceClass` CRD can reference specific interfaces and the controller can validate exporters against the contract — not just by checking labels, but by comparing actual `FileDescriptorProto` descriptors. Today, a driver declares that it implements `PowerInterface` by inheriting from the class, but there is no runtime or registration-time verification that the driver's `@export` methods actually match the interface contract. A typo in a method name, a missing parameter, or a wrong return type silently breaks clients at call time. The `FileDescriptorProto` from this JEP enables structural enforcement at every level of the DeviceClass mechanism:

  *At build time:* `jmp interface check` verifies that a Python interface matches its `.proto` definition. This extends to verifying that a driver implementation's `@export` methods match the interface proto — catching signature mismatches before code is shipped.

  *At exporter registration time:* The controller receives `FileDescriptorProto` descriptors in each driver's `DriverInstanceReport`. It compares these against the canonical `FileDescriptorProto` stored in a DeviceClass or InterfaceClass CRD to perform structural validation — comparing actual method signatures, parameter types, return types, and streaming semantics. A driver that claims to implement `power-v1` but is missing the `read()` streaming method would be flagged at registration, not discovered at test time.

  *At lease time:* A lease requesting a specific DeviceClass resolves to a set of required interface references, each with a canonical proto. The controller validates that every matched exporter's drivers produce compatible descriptors — ensuring that the leased device actually satisfies the contract the test code was generated against.

  *For driver certification:* A DeviceClass could declare compliance requirements: "this device provides `power-v1` at version `1.0.0` with these exact method signatures." A future registry could track which driver packages are certified against which interface versions, and `jmp validate` could verify local exporter configurations against the published DeviceClass contract before deployment.

  The strongly-typed protos from this JEP make all of this structural rather than convention-based. Instead of relying on class inheritance and label matching (which can drift silently), the system compares machine-readable schemas at every boundary.

- **Polyglot client code generation:** The `.proto` files produced by `jmp interface generate` feed directly into `protoc` for Kotlin, TypeScript, Rust, and other language stubs. A `jmp codegen` tool could wrap this pipeline.

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

The `.proto` files from `jmp interface generate` (this JEP) are compiled by `protoc` to produce native stubs. For `PowerInterface`:

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
# Auto-generated by jmp interface implement (or hand-written)
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
3. **Deprecation:** Mark `DriverCall` as deprecated. Migration guide published.
4. **Removal:** Remove `DriverCall` in a major version bump. All clients use native gRPC.

## Implementation Phases

| Phase | Deliverable                                                                                                     | Depends On     |
| ----- | --------------------------------------------------------------------------------------------------------------- | -------------- |
| 1a    | `DriverInterfaceMeta` + `DriverInterface` base class — type-safe interface marking with registry and validation | —              |
| 1b    | Migrate all existing interfaces to `DriverInterface` with full type annotations                                 | Phase 1a       |
| 2     | `@export` type info capture + type enforcement — store `ExportedMethodInfo`, reject unannotated methods         | —              |
| 3     | Type mapping module — Python types to protobuf field types, handling BaseModel, list, enum, UUID                | Phase 2        |
| 4     | `build_file_descriptor()` module — construct `FileDescriptorProto` from `DriverInterface` classes               | Phase 1a, 2, 3 |
| 5     | `jumpstarter/annotations/annotations.proto` — `resource_handle` field option                                    | —              |
| 6     | Doc comment extraction — docstrings to proto comments in builder                                                | Phase 4        |
| 7     | `DriverInstanceReport.file_descriptor_proto` field — embed descriptor in reports                                | Phase 4        |
| 8     | `jmp interface generate` CLI tool — Python → `.proto` source files                                              | Phase 4, 5, 6  |
| 9     | gRPC Server Reflection registration at exporter startup                                                         | Phase 4        |
| 10    | `jmp interface implement` CLI tool — `.proto` → Python interface + client + driver adapter (proto-first only)   | Phase 5, 6     |
| 11    | `jmp interface check` CLI tool — verify proto ↔ Python consistency                                              | Phase 8, 10    |

Phases 1a–1b establish the type-safe interface foundation. Phase 2 enforces type annotations on all `@export` methods. Phases 3–4 build the introspection core. Phases 5–7 deliver runtime schema exposure. Phase 8 provides the Python → proto CLI. Phases 9–11 complete the bidirectional tooling and runtime reflection.

## Implementation History

- 2026-04-06: JEP drafted
- 2026-04-07: JEP refined — added `DriverInterface` metaclass, type enforcement on `@export`, resource handle pattern, native gRPC migration sketch; fixed Pydantic BaseModel usage, NetworkInterface proto, driver adapter scope; expanded type mapping table and unresolved questions

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
