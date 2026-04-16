# JEP-0013: Native gRPC Services for Driver Interfaces

| Field             | Value                                                         |
| ----------------- | ------------------------------------------------------------- |
| **JEP**           | 0013                                                          |
| **Title**         | Native gRPC Services for Driver Interfaces                    |
| **Author(s)**     | @kirkbrauer (Kirk Brauer)                                     |
| **Status**        | Draft                                                         |
| **Type**          | Standards Track                                               |
| **Created**       | 2026-04-11                                                    |
| **Updated**       | 2026-04-11 (Phase 1-3 implementation review)                  |
| **Discussion**    | [Matrix](https://matrix.to/#/#jumpstarter:matrix.org)         |
| **Requires**      | JEP-0011 (Protobuf Introspection), JEP-0012 (ExporterClass Mechanism) |
| **Supersedes**    | —                                                             |
| **Superseded-By** | —                                                             |

---

## Abstract

This JEP replaces Jumpstarter's underlying driver wire protocol — the generic `ExporterService.DriverCall` / `StreamingDriverCall` dispatch — with native gRPC services generated from the `.proto` interface definitions that JEP-0011 already produces. Each driver interface (power, ADB, serial, flasher, etc.) becomes a real gRPC service on the exporter, with `protoc`-generated code handling serialization on both sides. Critically, **both the client-side and exporter-side programming models remain unchanged**: drivers continue to use `@export` decorated methods, and clients continue to use `DriverClient.call("method")` or typed client classes like `PowerClient`. The native gRPC translation is handled transparently by generated adapter code under the hood. A metadata-based UUID routing mechanism (`x-jumpstarter-driver-uuid`) disambiguates multiple instances of the same interface. `@exportstream` methods now use native gRPC bidi streaming with `StreamData { bytes payload }` messages, while the resource mechanism continues to use `RouterService.Stream`. This JEP eliminates the `Value` serialization layer at the transport level, enables standard gRPC tooling (per-method metrics, `grpcurl`, interceptors), and simplifies JEP-0014's polyglot codegen — new language clients can optionally use native `protoc` stubs directly, but are not required to.

## Motivation

Jumpstarter's current wire protocol routes all driver method calls through a single generic RPC:

```protobuf
service ExporterService {
  rpc DriverCall(DriverCallRequest) returns (DriverCallResponse);
  rpc StreamingDriverCall(StreamingDriverCallRequest) returns (stream StreamingDriverCallResponse);
}

message DriverCallRequest {
  string uuid = 1;           // target driver instance
  string method = 2;         // method name string
  repeated google.protobuf.Value args = 3;  // arguments as generic Values
}
```

This design served the project well during its Python-only phase — it's flexible, simple to implement, and requires no schema coordination between client and server. But it has fundamental limitations that block the project's evolution toward polyglot, type-safe device interaction:

**Double serialization.** Every call traverses: Python object -> JSON-like dict -> `google.protobuf.Value` -> wire -> `Value` -> JSON-like dict -> native object. The `encode_value`/`decode_value` functions in `jumpstarter/common/serde.py` and the Java `ValueCodec` perform this conversion on every call. With native gRPC services, the path becomes: proto message -> wire -> proto message (standard protobuf binary serialization, ~2-10x more efficient for structured data).

**No compile-time type safety.** Method names are strings (`self.call("on")`), arguments and results are `google.protobuf.Value`. A typo like `self.call("onn")` fails at runtime with a `NOT_FOUND` error, potentially minutes into a CI pipeline. With native stubs, `powerStub.on(Empty.getDefaultInstance())` is checked at compile time.

**Poor observability.** All calls appear as `/jumpstarter.v1.ExporterService/DriverCall` in traces and metrics. The actual method name is buried inside the request payload. With native services, each call appears distinctly — `/jumpstarter.interfaces.power.v1.PowerInterface/On` — enabling per-method latency histograms, error rate dashboards, and meaningful alerting.

**Polyglot barrier.** JEP-0014 (Polyglot Typed Device Wrappers) requires every new language to implement: (1) `Value` serialization codec, (2) `DriverCall` dispatch wrapper with string method mapping, (3) streaming call wrapper. These are ~150 lines of error-prone, language-specific code. With native gRPC services, standard `protoc` generates fully functional client stubs in any language — zero Jumpstarter-specific dispatch code needed.

**No standard tooling.** Cannot use `grpcurl` for ad-hoc testing, gRPC reflection for per-service discovery, standard gRPC interceptors for retry/deadline/auth per method, or API gateways that understand gRPC service definitions. All tooling must be custom-built around the generic `DriverCall` envelope.

JEP-0011 already solved the schema problem: every driver interface has a canonical `.proto` file defining a real gRPC service with typed messages. JEP-0012 solved the contract problem: ExporterClasses and DriverInterfaces define typed contracts validated by the controller. The missing piece is making those proto-defined services real — actually serving and calling them as native gRPC RPCs instead of treating them as documentation.

### User Stories

- **As a** test engineer using any language, **I want** my existing `DriverClient.call("on")` code to automatically benefit from native gRPC transport (better performance, per-method tracing) without any code changes, **so that** upgrading the exporter is all I need to do.

- **As a** Java/Kotlin test engineer, **I want** the option to use `protoc`-generated stubs for compile-time type safety and IDE auto-complete when writing new tests, **so that** I catch method name typos at compile time instead of at runtime.

- **As a** platform SRE, **I want to** see per-method gRPC metrics (latency for `PowerInterface/On`, error rate for `FlasherInterface/Flash`) in my monitoring stack, **so that** I can set meaningful alerts and identify which driver operations are degraded.

- **As a** driver developer, **I want** my existing `@export` decorated methods to serve native gRPC automatically without any changes to my driver code, **so that** I get the benefits of native transport without a rewrite.

- **As a** tools developer, **I want to** call `grpcurl -plaintext localhost:50051 jumpstarter.interfaces.power.v1.PowerInterface/On` to test a power driver directly, **so that** I can debug driver behavior without writing a full test harness.

- **As a** polyglot codegen maintainer (JEP-0014), **I want** the native gRPC transport to eliminate the need for per-language `ValueCodec` and `DriverCall` dispatch wrappers, **so that** new language support requires only session management and resource adapters (~80 lines).

## Proposal

### Design Principles

1. **No changes to driver code.** Existing `@export` decorated methods on drivers work unchanged. The generated server-side adapters bridge native gRPC services to existing driver methods transparently.

2. **No changes to client code.** Existing `DriverClient.call("method")` invocations and typed client classes (e.g., `PowerClient`) work unchanged. The framework transparently selects native gRPC transport when the exporter supports it.

3. **Opt-in typed access.** For officially-supported languages, `jmp codegen` generates typed client wrappers (e.g., `PowerClient`) that hide the raw proto stubs behind clean, idiomatic APIs. The existing string-based `call()` API also remains functional.

4. **Non-Python drivers are native gRPC.** Drivers in non-Python languages implement the `protoc`-generated servicer interface directly — standard gRPC, no Jumpstarter-specific base classes. Python drivers retain the `@export` decorator; a generated adapter bridges them to native gRPC transparently.

5. **Transparent negotiation.** The client detects native service availability from the exporter's `DriverInstanceReport` and selects the optimal transport automatically. Old clients work with new exporters; new clients work with old exporters.

### Overview

Five changes, phased for backward compatibility:

1. **Server-side adapter generation** — `jmp interface implement` generates gRPC servicer adapters that bridge proto service stubs to existing `@export` driver methods. Drivers remain unchanged — the adapter translates between proto messages and the driver's Python method signatures. The exporter registers these alongside `ExporterService`.

2. **UUID routing via gRPC metadata** — A server-side `DriverRegistry` reads `x-jumpstarter-driver-uuid` from call metadata and routes to the correct driver instance when multiple drivers implement the same interface.

3. **Client-side transparent upgrade** — The existing `DriverClient.call("method")` API and typed client classes (e.g., `PowerClient`) remain the primary programming interface. Under the hood, the framework detects native service availability and transparently routes calls through native gRPC stubs instead of `DriverCall` when the exporter supports it. Client code does not change. Polyglot clients in new languages *may optionally* use `protoc`-generated stubs directly for maximum type safety, but this is not required — the `DriverClient.call()` pattern works in any language.

4. **Dual-stack compatibility** — Exporters serve both `ExporterService` (legacy) and native per-interface services simultaneously. The client framework auto-detects native service availability from `DriverInstanceReport.native_services` and transparently selects the optimal transport. Old exporters get `DriverCall`; new exporters get native gRPC. This is invisible to client code.

5. **Resource protocol migration** — Resource handles (currently passed as string UUIDs through `Value`) get a proto-native `ResourceHandle` message while continuing to use `RouterService.Stream` for actual byte transport.

### Router Transparency

The router between client and exporter (`controller/internal/service/router_service.go`) operates at the byte-stream level — it pairs two `RouterService.Stream` connections and forwards raw gRPC frames via the `Forward()` function. It does not inspect or understand the services within the tunnel.

The client establishes a `RouterService.Stream` tunnel to the exporter, then opens a standard gRPC channel on a local Unix socket bridged to that tunnel. Any gRPC service registered on the exporter's server is automatically accessible through this tunnel.

**No changes to the router are required.** The exporter simply registers additional gRPC services on its existing server, and they become reachable through the same tunnel that already carries `ExporterService` and `RouterService` traffic.

### Wire Protocol Changes

#### DriverInstanceReport Extension

A new `native_services` field is added to `DriverInstanceReport`:

```protobuf
message DriverInstanceReport {
  string uuid = 1;
  optional string parent_uuid = 2;
  map<string, string> labels = 3;
  optional string description = 4;
  map<string, string> methods_description = 5;
  optional bytes file_descriptor_proto = 6;
  repeated string native_services = 7;  // NEW: fully-qualified gRPC service names
}
```

When `native_services` is populated (e.g., `["jumpstarter.interfaces.power.v1.PowerInterface"]`), clients know they can use native gRPC stubs for that driver instead of `DriverCall`. Old exporters leave this field empty; old clients ignore it. This is the negotiation mechanism — additive and backward-compatible at the protobuf level.

#### ExporterService: Unchanged

`ExporterService` remains fully registered and functional. All seven existing RPCs (`GetReport`, `DriverCall`, `StreamingDriverCall`, `LogStream`, `Reset`, `GetStatus`, `EndSession`) continue to work exactly as before. Legacy clients that don't understand `native_services` use `DriverCall` without any change.

#### Native gRPC Services: New

Each driver interface's `.proto` file (generated by JEP-0011) already defines a gRPC service. For example, `power/v1/power.proto`:

```protobuf
service PowerInterface {
  rpc Off(google.protobuf.Empty) returns (google.protobuf.Empty);
  rpc On(google.protobuf.Empty) returns (google.protobuf.Empty);
  rpc Read(google.protobuf.Empty) returns (stream PowerReading);
}
```

The exporter registers actual implementations of these services alongside `ExporterService`. The `PowerInterface` service becomes callable as native gRPC RPCs through the same tunnel.

#### UUID Routing via gRPC Metadata

Since an exporter may have multiple drivers implementing the same interface (e.g., two power supplies), the client sends the target driver UUID as gRPC metadata:

```
x-jumpstarter-driver-uuid: <uuid-string>
```

The generated servicer reads this metadata to route to the correct driver instance. If only one instance of an interface exists on the exporter, the metadata can be omitted — the servicer defaults to the single instance. If multiple instances exist and no UUID is provided, the servicer returns `FAILED_PRECONDITION` with a descriptive message listing available instances.

#### `@exportstream` Native Bidi Streaming

Methods decorated with `@exportstream` (e.g., `PySerial.connect()`, `TcpNetwork.connect()`) produce raw bidirectional byte streams. With JEP-1's `StreamData { bytes payload }` message type, these are now served as **native gRPC bidi streaming RPCs** — the same transport as unary and server-streaming methods. In the interface proto they appear as `rpc Connect(stream StreamData) returns (stream StreamData)`.

The generated native servicer implements the bidi handler directly: it calls the driver's `@exportstream` context manager, spawns concurrent tasks for inbound (client→driver) and outbound (driver→client) byte forwarding via `StreamData.payload`, and sends initial metadata eagerly (via `context.send_initial_metadata(())`) so that clients like tonic can establish the connection without deadlocking. Non-Python clients call the native `Connect` endpoint directly, bridging local TCP/UDP sockets to the bidi stream for port forwarding — no `RouterService.Stream` dispatch needed.

### CLI Interface

The existing `jmp interface implement` command (JEP-0011) is extended to generate server-side servicer adapters:

```bash
# Generate client, interface, driver adapter, AND servicer adapter
jmp interface implement --proto power/v1/power.proto --output src/gen/

# Generate servicer adapter only
jmp interface implement --proto power/v1/power.proto --servicer-only --output src/gen/
```

No new CLI subcommand is needed — the servicer generation is an additional output artifact from the existing `implement` command.

### Generated Server-Side Adapter

For each driver interface, `jmp interface implement` generates a gRPC servicer adapter that bridges the proto service stubs to existing `@export` driver methods. The adapter handles three categories:

#### Unary RPCs

```python
# generated: jumpstarter_driver_power/servicer.py
from inspect import iscoroutinefunction, isasyncgenfunction
import power_pb2
import power_pb2_grpc
from google.protobuf.empty_pb2 import Empty
from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.power.v1.PowerInterface"

class PowerInterfaceServicer(power_pb2_grpc.PowerInterfaceServicer):
    """Auto-generated gRPC servicer for PowerInterface.

    Bridges native gRPC calls to @export driver methods.
    Do not edit — regenerate with `jmp interface implement`.
    """

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def On(self, request: Empty, context: grpc.aio.ServicerContext) -> Empty:
        driver = self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.on):
            await driver.on()
        else:
            driver.on()
        return Empty()

    async def Off(self, request: Empty, context: grpc.aio.ServicerContext) -> Empty:
        driver = self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.off):
            await driver.off()
        else:
            driver.off()
        return Empty()

# Register the adapter at import time so the Session can discover it.
def _register():
    from jumpstarter_driver_power.driver import PowerInterface
    register_servicer_adapter(
        interface_class=PowerInterface,
        service_name=SERVICE_NAME,
        servicer_factory=PowerInterfaceServicer,
        add_to_server=power_pb2_grpc.add_PowerInterfaceServicer_to_server,
    )

_register()
```

> **Implementation note:** The servicer handles both sync and async driver methods via `iscoroutinefunction`/`isasyncgenfunction` introspection. This is necessary because `@export` drivers may be either sync or async (e.g., `MockPower` vs `SyncMockPower`).

#### Server-Streaming RPCs

```python
    async def Read(self, request: Empty, context: grpc.aio.ServicerContext):
        driver = self._registry.resolve(context, SERVICE_NAME)
        if isasyncgenfunction(type(driver).read):
            async for reading in driver.read():
                yield power_pb2.PowerReading(
                    voltage=reading.voltage,
                    current=reading.current,
                )
        else:
            for reading in driver.read():
                yield power_pb2.PowerReading(
                    voltage=reading.voltage,
                    current=reading.current,
                )
```

#### `@exportstream` Methods (Native Bidi Handler)

For bidi-streaming methods that represent `@exportstream` constructors, the servicer implements a native handler that bridges the gRPC bidi stream to the driver's byte stream:

```python
    async def Connect(self, request_iterator, context: grpc.aio.ServicerContext):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        async with driver.connect() as stream:
            # Send initial metadata eagerly so bidi clients don't block
            await context.send_initial_metadata(())
            async def _inbound():
                async for msg in request_iterator:
                    await stream.send(msg.payload)
                await stream.send_eof()
            async with anyio.create_task_group() as tg:
                tg.start_soon(_inbound)
                try:
                    while True:
                        data = await stream.receive()
                        yield network_pb2.StreamData(payload=data)
                except (anyio.EndOfStream, anyio.ClosedResourceError):
                    pass
                tg.cancel_scope.cancel()
```

### DriverRegistry

The `DriverRegistry` is a new component in the exporter session that maps `(service_name, uuid)` pairs to driver instances:

```python
class DriverRegistry:
    """Routes native gRPC calls to the correct driver instance."""

    def __init__(self):
        self._by_uuid: dict[str, tuple[str, Any]] = {}      # uuid -> (service, driver)
        self._by_service: dict[str, dict[str, Any]] = {}     # service -> {uuid: driver}

    def register(self, uuid: str, service_name: str, driver: Any):
        self._by_uuid[uuid] = (service_name, driver)
        self._by_service.setdefault(service_name, {})[uuid] = driver

    def resolve(self, context: grpc.aio.ServicerContext, service_name: str) -> Any:
        """Resolve the target driver from gRPC call metadata."""
        metadata = dict(context.invocation_metadata())
        uuid = metadata.get("x-jumpstarter-driver-uuid")

        drivers = self._by_service.get(service_name, {})
        if not drivers:
            context.abort(grpc.StatusCode.NOT_FOUND, f"no driver registered for {service_name}")

        if uuid:
            driver = drivers.get(uuid)
            if driver is None:
                context.abort(grpc.StatusCode.NOT_FOUND, f"driver {uuid} not found for {service_name}")
            return driver

        if len(drivers) == 1:
            return next(iter(drivers.values()))

        uuids = list(drivers.keys())
        context.abort(
            grpc.StatusCode.FAILED_PRECONDITION,
            f"multiple drivers for {service_name}, specify x-jumpstarter-driver-uuid: {uuids}",
        )
```

#### ServicerAdapterInfo Registry

The `DriverRegistry` module also provides a **global servicer adapter registry** that decouples servicer discovery from driver interface classes. Rather than attaching `_native_servicer_class` attributes to interface classes (as originally proposed), generated servicer modules register themselves at import time via `register_servicer_adapter()`:

```python
@dataclass(frozen=True)
class ServicerAdapterInfo:
    """Metadata about a generated servicer adapter for a DriverInterface."""
    interface_class: type          # e.g., PowerInterface
    service_name: str              # e.g., "jumpstarter.interfaces.power.v1.PowerInterface"
    servicer_factory: Callable[[DriverRegistry], Any]  # creates the servicer
    add_to_server: Callable[[Any, grpc.aio.Server], None]  # the add_XServicer_to_server fn

def register_servicer_adapter(interface_class, service_name, servicer_factory, add_to_server):
    """Called by generated servicer modules at import time."""
    ...

def get_servicer_adapter(interface_class) -> ServicerAdapterInfo | None:
    """Look up the servicer adapter for a DriverInterface class."""
    ...
```

This design was chosen during implementation over the originally-proposed `interface_cls._native_servicer_class` attribute approach because:
1. It avoids monkey-patching interface classes, keeping them clean.
2. It allows servicer adapters to live in separate packages from their interfaces.
3. Registration at import time means the Session can discover adapters by auto-importing `{package}.servicer` modules (convention-based discovery).

### Client-Side Usage

The key design principle is that **client code does not change**. The native gRPC transport is selected transparently by the framework based on exporter capabilities. Existing `DriverClient.call()` invocations and typed client classes work exactly as before.

#### Python (transparent — no code changes)

Existing Python client code works unchanged. The framework detects native service support and transparently uses native gRPC calls instead of `DriverCall`:

```python
async with client.lease("my-exporter") as session:
    # Exactly the same API as today — no changes needed
    power = session.driver("power", PowerClient)
    await power.on()                    # native gRPC under the hood (if available)
    async for reading in power.read():  # transparent streaming upgrade
        print(reading.voltage)
```

Under the hood, `PowerClient.on()` still calls `self.call("on")`, but the `AsyncDriverClient` base class detects that the exporter supports `jumpstarter.interfaces.power.v1.PowerInterface` natively and translates the `call("on")` into a native `PowerInterface/On` gRPC call with proper proto message serialization — instead of wrapping it in a `DriverCallRequest` with `Value` args. This is invisible to both the client code and the driver implementation.

The transparent routing is implemented via a **NativeClientAdapterInfo registry** (in `jumpstarter.client.native`), symmetric to the server-side `ServicerAdapterInfo` registry. Each driver package provides a `client_native` module that registers method-to-stub mappings at import time:

```python
# jumpstarter_driver_power/client_native.py
from jumpstarter.client.native import register_native_client_adapter

async def _call_on(stub, uuid, *args):
    metadata = (("x-jumpstarter-driver-uuid", str(uuid)),)
    await stub.On(Empty(), metadata=metadata)
    return None

async def _stream_read(stub, uuid, *args):
    metadata = (("x-jumpstarter-driver-uuid", str(uuid)),)
    async for reading in stub.Read(Empty(), metadata=metadata):
        yield PowerReading(voltage=reading.voltage, current=reading.current)

register_native_client_adapter(
    service_name=SERVICE_NAME,
    stub_class=power_pb2_grpc.PowerInterfaceStub,
    call_handlers={"on": _call_on, "off": _call_off},
    streaming_call_handlers={"read": _stream_read},
)
```

During client tree construction (`client_from_channel`), when a `DriverInstanceReport` includes `native_services`, the client calls `_setup_native_services()` which auto-imports `{package}.client_native` and binds native stubs to the channel. After setup, `call_async()` and `streamingcall_async()` check `_native_call_handlers` / `_native_streaming_handlers` first, falling back to legacy `DriverCall` when no native handler is registered for a method.

#### Java/Kotlin (transparent — same DriverClient.call() API)

The existing Java `DriverClient.call("on")` API continues to work. The framework handles the native gRPC translation:

```java
// Existing API — unchanged, native gRPC under the hood
ExporterSession session = ExporterSession.fromEnvironment();
DriverClient power = session.driverClientByName("power");
power.call("on");
List<Object> readings = power.streamingCallToList("read");
```

For typed access, teams use the **generated typed client** (see "Generated Typed Clients" below). The generated client hides proto stubs behind an idiomatic API — no `Empty.getDefaultInstance()` or `ServiceStub` exposed:

```java
// Generated typed client — clean, idiomatic API
PowerClient power = new PowerClient(session, "power");
power.on();                                  // typed, IDE auto-complete
for (PowerReading reading : power.read()) {  // typed streaming
    System.out.println(reading.getVoltage());
}
```

```kotlin
// Generated typed client — Kotlin coroutines
val power = PowerClient(session, "power")
power.on()
power.read().collect { reading ->
    println(reading.voltage)
}
```

The generated `PowerClient` wraps the `protoc`-generated stub internally, handling channel setup, UUID metadata injection, and proto message construction. Users never interact with raw gRPC stubs directly.

#### grpcurl (ad-hoc testing)

```bash
# Discover available services
grpcurl -plaintext localhost:50051 list
# Output:
# jumpstarter.interfaces.power.v1.PowerInterface
# jumpstarter.v1.ExporterService
# grpc.reflection.v1alpha.ServerReflection

# Call power on
grpcurl -plaintext \
  -H "x-jumpstarter-driver-uuid: <uuid>" \
  localhost:50051 jumpstarter.interfaces.power.v1.PowerInterface/On

# Stream power readings
grpcurl -plaintext \
  -H "x-jumpstarter-driver-uuid: <uuid>" \
  localhost:50051 jumpstarter.interfaces.power.v1.PowerInterface/Read
```

### Native Drivers and Generated Typed Clients

#### Python Drivers: `@export` Retained

Python drivers continue to use the `@export` decorator — the same API as today. The difference is purely at the transport layer: the generated server-side adapter translates `@export` methods into native gRPC services automatically. Driver authors don't need to think about proto messages or gRPC servicers:

```python
# jumpstarter_driver_power/driver.py — same as today, no changes
from jumpstarter.driver import Driver, export

class PowerDriver(Driver):
    @export
    async def on(self) -> None:
        """Energize the power relay."""
        GPIO.output(self._gpio_pin, GPIO.HIGH)

    @export
    async def off(self) -> None:
        """De-energize the power relay."""
        GPIO.output(self._gpio_pin, GPIO.LOW)

    @export
    async def read(self) -> AsyncGenerator[PowerReading, None]:
        """Stream real-time power measurements."""
        while True:
            yield PowerReading(voltage=self._read_voltage(), current=self._read_current())
            await asyncio.sleep(0.1)
```

The generated servicer adapter (from `jmp interface implement`) bridges this to the native `PowerInterface` gRPC service transparently. The `@export` decorator provides the interface contract; the adapter handles the proto serialization.

#### Non-Python Drivers: Native gRPC Servicers

For non-Python languages, drivers implement the `protoc`-generated service interface directly. This is the only path for non-Python drivers and requires no Jumpstarter-specific base classes:

```kotlin
// dev/jumpstarter/drivers/power/GpioPowerDriver.kt
package dev.jumpstarter.drivers.power

import com.google.protobuf.Empty
import jumpstarter.interfaces.power.v1.PowerInterfaceGrpcKt.PowerInterfaceCoroutineImplBase
import jumpstarter.interfaces.power.v1.Power.PowerReading
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

class GpioPowerDriver(private val gpioPin: Int) : PowerInterfaceCoroutineImplBase() {

    override suspend fun on(request: Empty): Empty {
        Gpio.output(gpioPin, HIGH)
        return Empty.getDefaultInstance()
    }

    override suspend fun off(request: Empty): Empty {
        Gpio.output(gpioPin, LOW)
        return Empty.getDefaultInstance()
    }

    override fun read(request: Empty): Flow<PowerReading> = flow {
        while (true) {
            val (voltage, current) = readAdc()
            emit(PowerReading.newBuilder()
                .setVoltage(voltage)
                .setCurrent(current)
                .build())
            delay(100)
        }
    }
}
```

This is a standard Kotlin gRPC service implementation — it can be compiled and tested with standard gRPC tooling, with no dependency on the Jumpstarter Python framework.

#### Generated Typed Clients

For officially-supported client languages, `jmp codegen` (JEP-0014) generates **typed client wrapper classes** that hide the raw `protoc` stubs behind an idiomatic API. Users interact with `PowerClient`, not `PowerInterfaceGrpc.PowerInterfaceBlockingStub`. The generated client handles stub creation, UUID metadata injection, and proto message construction internally.

**Python** — generated typed client wraps the native stub:

```python
# generated: jumpstarter_driver_power/client.py
class PowerClient:
    """Typed client for the PowerInterface driver."""

    def __init__(self, session: ExporterSession, driver_name: str):
        self._stub = session.native_stub(driver_name, power_pb2_grpc.PowerInterfaceStub)

    async def on(self) -> None:
        """Energize the power relay."""
        await self._stub.On(Empty())

    async def off(self) -> None:
        """De-energize the power relay."""
        await self._stub.Off(Empty())

    async def read(self) -> AsyncIterator[PowerReading]:
        """Stream real-time power measurements."""
        async for proto_reading in self._stub.Read(Empty()):
            yield PowerReading(voltage=proto_reading.voltage, current=proto_reading.current)
```

**Java** — generated typed client with idiomatic Java API:

```java
// generated: dev/jumpstarter/drivers/power/PowerClient.java
public class PowerClient implements AutoCloseable {
    private final PowerInterfaceGrpc.PowerInterfaceBlockingStub stub;

    public PowerClient(ExporterSession session, String driverName) {
        this.stub = session.nativeStub(driverName, PowerInterfaceGrpc::newBlockingStub);
    }

    /** Energize the power relay. */
    public void on() {
        stub.on(Empty.getDefaultInstance());
    }

    /** De-energize the power relay. */
    public void off() {
        stub.off(Empty.getDefaultInstance());
    }

    /** Stream real-time power measurements. */
    public Iterator<PowerReading> read() {
        return stub.read(Empty.getDefaultInstance());
    }
}
```

**Kotlin** — generated typed client with coroutines:

```kotlin
// generated: dev/jumpstarter/drivers/power/PowerClient.kt
class PowerClient(session: ExporterSession, driverName: String) : AutoCloseable {
    private val stub = session.nativeStub(driverName, PowerInterfaceGrpcKt::PowerInterfaceCoroutineStub)

    /** Energize the power relay. */
    suspend fun on() {
        stub.on(Empty.getDefaultInstance())
    }

    /** De-energize the power relay. */
    suspend fun off() {
        stub.off(Empty.getDefaultInstance())
    }

    /** Stream real-time power measurements. */
    fun read(): Flow<PowerReading> = stub.read(Empty.getDefaultInstance())
}
```

The end-user experience is clean and idiomatic — no `Empty.getDefaultInstance()`, no `ServiceStub`, no metadata:

```python
# Python test code
async with client.lease("my-exporter") as session:
    power = PowerClient(session, "power")
    await power.on()
    async for reading in power.read():
        print(reading.voltage)
```

```java
// Java test code
try (ExporterSession session = ExporterSession.fromEnvironment()) {
    PowerClient power = new PowerClient(session, "power");
    power.on();
    for (PowerReading reading : power.read()) {
        System.out.println(reading.getVoltage());
    }
}
```

```kotlin
// Kotlin test code
val session = ExporterSession.fromEnvironment()
val power = PowerClient(session, "power")
power.on()
power.read().collect { reading ->
    println(reading.voltage)
}
```

#### Comparison: Python Drivers vs. Non-Python Drivers vs. Clients

| Aspect | Python drivers | Non-Python drivers | Generated typed clients |
|--------|---------------|-------------------|------------------------|
| API style | `@export` decorators (unchanged) | `protoc`-generated servicer | Generated wrapper classes |
| Proto awareness | None — adapter handles it | Direct proto message types | Hidden — wrapper handles it |
| Framework dependency | Jumpstarter `Driver` base | None (standard gRPC) | Minimal (session + wrapper) |
| Language | Python only | Any gRPC language | Python, Java, Kotlin, TS, Rust |
| Code generation | Servicer adapter auto-generated | Driver is hand-written | Client wrapper auto-generated |

Both driver patterns coexist — a Kotlin native driver can serve an exporter alongside a Python `@export` driver. All generated typed clients work against either driver type, since both serve the same native gRPC interface.

### Exporter Registration Flow

The exporter's `Session` class registers native services during startup. The registration is handled by a dedicated `_register_native_services()` method that uses the `ServicerAdapterInfo` registry for discovery:

```python
def _register_native_services(self, server):
    """Discover and register native gRPC servicer adapters for all drivers."""
    registry = DriverRegistry()
    registered_services: set[str] = set()

    for _uuid, _parent, _name, instance in self.root_device.enumerate():
        interface_class = instance._get_interface_class()
        if interface_class is None:
            continue

        # Convention: auto-import {top_package}.servicer to trigger registration
        self._try_import_servicer(interface_class)

        adapter_info = get_servicer_adapter(interface_class)
        if adapter_info is None:
            continue

        # Register the driver instance in the registry
        registry.register(str(instance.uuid), adapter_info.service_name, instance)

        # Register the servicer on the gRPC server (once per service)
        if adapter_info.service_name not in registered_services:
            servicer = adapter_info.servicer_factory(registry)
            adapter_info.add_to_server(servicer, server)
            registered_services.add(adapter_info.service_name)

    return registry, list(registered_services)
```

> **Implementation note:** The Session uses convention-based auto-import (`_try_import_servicer`) to discover servicer modules. The convention is `{top_package}.servicer` — e.g., for `jumpstarter_driver_power.driver.PowerInterface`, it imports `jumpstarter_driver_power.servicer`. This triggers the `register_servicer_adapter()` call at import time, making the adapter discoverable via `get_servicer_adapter()`.

This method is called from `_serve_grpc_server_async()` and all `serve_*` methods, with native service names passed to `_register_reflection()`:

```python
async def _serve_grpc_server_async(self, server):
    # Legacy services (unchanged)
    jumpstarter_pb2_grpc.add_ExporterServiceServicer_to_server(self, server)
    router_pb2_grpc.add_RouterServiceServicer_to_server(self, server)

    # Native interface services (new)
    _registry, native_service_names = self._register_native_services(server)

    # gRPC reflection includes native service names
    self._register_reflection(server, extra_services=native_service_names)

    await server.start()
    ...
```

The `DriverInstanceReport` is extended to include `native_services`. The `report()` method on the `Driver` base class checks the `ServicerAdapterInfo` registry:

```python
def report(self) -> DriverInstanceReport:
    native_services = []
    interface_class = self._get_interface_class()
    if interface_class is not None:
        adapter_info = get_servicer_adapter(interface_class)
        if adapter_info is not None:
            native_services.append(adapter_info.service_name)

    return DriverInstanceReport(
        uuid=str(self.uuid),
        labels=self.labels,
        # ... existing fields ...
        file_descriptor_proto=fd_bytes,
        native_services=native_services,
    )
```

### API / Protocol Changes

| Change | Type | Backward Compatible |
|--------|------|-------------------|
| `DriverInstanceReport.native_services` field (field 7) | Proto addition | Yes — new optional repeated field |
| Native gRPC services on exporter | New gRPC services | Yes — additive, existing services unchanged |
| `x-jumpstarter-driver-uuid` metadata convention | New metadata key | Yes — servers that don't understand it ignore it |
| `ExporterService` RPCs | No change | Yes — fully preserved |
| `RouterService` | No change | Yes — fully preserved |

### Hardware Considerations

This JEP is a wire protocol change with no hardware impact. The generated servicer adapters call the same `@export` driver methods that `DriverCall` dispatch calls today — the hardware interaction path is identical. The overhead of registering additional gRPC services on the exporter server is negligible (a few KB of memory per service).

## Design Details

### Architecture

```
Client                         Router                      Exporter
  |                              |                            |
  |--- Dial (controller) ------->|                            |
  |                              |                            |
  |==== RouterService.Stream === byte tunnel ================>|
  |                              |                            |
  |--- gRPC channel over tunnel ------------------------------>|
  |                                                           |
  | Legacy path (preserved):                                  |
  |--- ExporterService/GetReport ---------------------------->|
  |--- ExporterService/DriverCall --------------------------->|
  |--- ExporterService/StreamingDriverCall ------------------>|
  |                                                           |
  | Native path (new):                                        |
  |--- PowerInterface/On ---- [metadata: uuid] -------------->|
  |--- PowerInterface/Read -- [metadata: uuid] -------------->|
  |--- FlasherInterface/Flash [metadata: uuid] -------------->|
  |                                                           |
  | Stream path (unchanged):                                  |
  |--- RouterService/Stream --------------------------------->|
```

### Serialization Comparison

| Aspect | `DriverCall` (current) | Native gRPC (proposed) |
|--------|----------------------|----------------------|
| Request format | `DriverCallRequest{uuid, method, args: [Value]}` | `Empty{}` or `FlashRequest{source, target}` |
| Encoding | Python -> JSON -> Value -> protobuf -> wire | Proto message -> protobuf -> wire |
| Type safety | None (runtime `NOT_FOUND` on typo) | Transport-level (compile-time opt-in via native stubs) |
| Method dispatch | `getattr(driver, request.method)` | gRPC service method routing |
| Trace label | `ExporterService/DriverCall` | `PowerInterface/On` |
| Per-method metrics | Requires custom parsing | Standard gRPC interceptors |
| Tooling | Custom only | `grpcurl`, gRPC reflection, standard interceptors |

### Client-Side Logic Audit: Impact on Driver Tiers

JEP-0014 identified four tiers of client-side complexity. Native gRPC services affect each tier differently:

**Tier 1 (Pure delegation):** Existing `self.call()` wrappers in `StorageMuxClient`, `NetworkClient`, `CompositeClient` continue to work — the framework transparently routes them through native gRPC. For new polyglot clients (JEP-0014), these can optionally use standard protoc-generated stubs directly.

**Tier 2 (Light orchestration):** `PowerClient.cycle()` still needs to be moved server-side (same recommendation as JEP-0014). `PowerClient.read()` is already a server-streaming RPC in the proto — the native stub handles it directly with typed `PowerReading` messages instead of `Value` deserialization.

**Tier 3 (Resource mechanism):** Resource handles become proto-native (see "Resource Handle Migration" below). The resource adapter (~80 lines per language) remains, but `Value`-based handle passing is eliminated.

**Tier 4 (Complex orchestration):** Out of scope. `BaseFlasherClient` and device-specific composites remain language-specific. They continue to use `DriverClient.call()` (which transparently benefits from native gRPC transport) or can optionally use native stubs for underlying interface calls.

### Resource Handle Migration

Currently, resource handles for flash/storage operations are passed as string UUIDs through `google.protobuf.Value`:

```python
# Current: resource handle as string through Value serialization
async with client.resource_async(stream, content_encoding=compression) as res:
    self.call("flash", res)  # res is a string UUID, encoded as Value
```

With native services, the resource handle gets a proto-native representation. The `jumpstarter.annotations.resource_handle` field option (already defined in `annotations/annotations.proto` at field number 50000) marks string fields that carry resource handles. The generated servicer recognizes these annotations and handles resource negotiation before dispatching to the driver.

The `FlashRequest` message already has the right shape:

```protobuf
message FlashRequest {
  string source = 1;    // annotated with resource_handle
  optional string target = 2;
}
```

The client opens a `RouterService.Stream` channel for the resource, then passes the stream UUID as the `source` field in the native `FlashRequest`. The servicer adapter resolves the UUID to a server-side stream before calling the driver's `flash()` method. This preserves the existing resource mechanism while eliminating the `Value` wrapping layer.

### Error Handling

The generated servicer adapters translate Python exceptions to standard gRPC status codes:

| Python Exception | gRPC Status Code | When |
|-----------------|------------------|------|
| `AttributeError` / method not found | `UNIMPLEMENTED` | Driver doesn't implement the RPC |
| `TypeError` / wrong arguments | `INVALID_ARGUMENT` | Request message has wrong field values |
| Unknown UUID in metadata | `NOT_FOUND` | `x-jumpstarter-driver-uuid` doesn't match any driver |
| Multiple instances, no UUID | `FAILED_PRECONDITION` | Ambiguous routing, client must specify UUID |
| Driver internal error | `INTERNAL` | Unexpected exception in driver method |
| Exporter not ready | `UNAVAILABLE` | Session not yet initialized |

### Security Implications

No new attack surface is introduced. Native gRPC services flow through the same authenticated router tunnel as `ExporterService`. The `x-jumpstarter-driver-uuid` metadata is trusted because it arrives over an already-authenticated connection — the router token authenticates the stream, and the exporter only accepts connections from the router. The UUID is not a secret; it's a routing hint within an authenticated session.

## Test Plan

### Unit Tests

- **DriverRegistry:** UUID resolution, single-instance default, multi-instance disambiguation, unknown UUID, missing metadata, concurrent access.
- **Generated servicer adapters:** Request deserialization, response serialization for all field types (primitives, nested messages, enums). Server-streaming lifecycle. Error translation from Python exceptions to gRPC status codes.
- **Client detection:** `native_services` field parsing, stub selection based on field presence, fallback to legacy `DriverCall` when field is empty.
- **Backward compatibility:** Legacy `DriverClient.call()` still works against exporters that serve both legacy and native services.

### Integration Tests

- **End-to-end native call through router tunnel:** Power on/off/read cycle using native stubs through a real router tunnel.
- **Mixed client scenario:** Legacy Python client and native Java client calling the same exporter simultaneously. Verify both produce correct results.
- **Multi-instance routing:** Exporter with two power drivers. Verify UUID-based disambiguation works via metadata. Verify error when multiple instances exist and no UUID is provided.
- **`@exportstream` alongside native services:** Serial stream via existing `RouterService.Stream` while power calls go through native gRPC stubs.
- **Resource handle via native service:** Flash operation with `ResourceHandle` field through native `FlasherInterface/Flash` RPC.
- **gRPC reflection:** Verify `grpcurl list` shows all registered native services on a running exporter.

### Hardware-in-the-Loop Tests

- Real power driver (e.g., DutlinkPower) via native gRPC stubs — verify hardware responds correctly.
- Flash operation with resource handles through native protocol.
- Serial stream via `@exportstream` alongside native power calls.

### Manual Verification

- `grpcurl` can discover and call native services on a running exporter (both local and through router tunnel).
- Per-method labels appear in gRPC metrics/traces.
- IDE auto-complete works with `protoc`-generated Java/Kotlin stubs.

## Graduation Criteria

### Experimental

- Server-side servicer adapters generated for at least power, serial, and flasher interfaces.
- Native gRPC calls work through the router tunnel in integration tests.
- Legacy `DriverCall` continues to work unchanged on the same exporter.
- `native_services` field populated in `DriverInstanceReport` and visible in `GetReport`.
- gRPC reflection lists native services.
- At least one non-Python driver (implementing the `protoc`-generated servicer directly) is registered and callable on an exporter alongside Python `@export` drivers.
- `grpcurl` can call native services on a running exporter.

### Stable

- All bundled driver interfaces have generated servicer adapters.
- Python `DriverClient` transparently routes through native gRPC when available, with automatic fallback to `DriverCall` for old exporters. Existing client code works unchanged.
- Java `DriverClient` transparently routes through native gRPC. Generated typed clients (e.g., `PowerClient`) available for idiomatic typed access.
- Existing `@export` driver implementations work unchanged — no driver code modifications needed.
- Per-method gRPC metrics demonstrated in monitoring (Prometheus, OpenTelemetry, or similar).
- Resource handle migration complete for flash/storage operations.
- `DriverCall` and `StreamingDriverCall` marked deprecated in proto with documented removal timeline.
- Cross-language interop tested: Python exporter serving native services called from Java and Kotlin clients using both `DriverClient.call()` and native stubs.
- At least one non-Python native-first driver (e.g., Kotlin) is registered and callable alongside Python `@export` drivers on the same exporter.

## Backward Compatibility

This JEP is **fully backward compatible** — both in wire protocol and programming model:

- **Driver-side (exporter):** Existing `@export` decorated driver methods work unchanged. The generated servicer adapters bridge native gRPC to the existing driver methods transparently. No driver code modifications are needed.

- **Client-side:** Existing `DriverClient.call("method")` code and typed client classes (e.g., `PowerClient`) work unchanged. The framework transparently selects native gRPC transport when the exporter supports it, falling back to `DriverCall` otherwise. Client code does not need to know or care which transport is used.

- **Exporter-side (protocol):** Legacy `ExporterService` remains registered and functional alongside native services. Old clients that don't understand `native_services` continue to use `DriverCall` without any change.

- **Proto compatibility:** The `.proto` files from JEP-0011 are unchanged. They already define gRPC services; this JEP implements those services for real instead of using them as description-only. The `DriverInstanceReport.native_services` field (field 7) is a new optional repeated field — additive and backward-compatible at the protobuf level.

- **Router:** Zero changes. The router is a transport-level byte forwarder; it does not inspect or understand the services flowing through the tunnel.

- **Controller:** Minimal change — the `DriverInstanceReport` gains `native_services` (field 7), which the controller stores and forwards to clients via `GetReport`. No new validation logic is required.

- **Deprecation timeline:** `DriverCall` and `StreamingDriverCall` will be marked deprecated in proto once the stable graduation criteria are met. A removal timeline (minimum one major version) will be communicated in the deprecation notice.

## Rejected Alternatives

### Separate gRPC port per interface

Running each interface on its own gRPC server/port was considered for isolation. Rejected because it would require multiple tunnel connections through the router, complicating connection management and increasing resource usage on exporters. A single gRPC server with multiple registered services is the standard gRPC pattern.

### UUID in every request message

Putting the driver UUID in every request message (e.g., `FlashRequest.driver_uuid`) was considered. Rejected because it would modify all interface protos, preventing the use of unmodified `protoc`-generated stubs. The metadata approach keeps protos clean and works with any standard gRPC client.

### Immediate DriverCall removal

Removing `DriverCall` without a dual-stack period was considered for simplicity. Rejected because it would break all existing clients simultaneously. The dual-stack approach allows gradual migration — clients upgrade at their own pace, and old clients continue to work.

### Custom binary protocol instead of gRPC

A custom binary protocol optimized for driver dispatch was considered. Rejected because it would lose all gRPC ecosystem benefits (tooling, interceptors, reflection, standard stubs in dozens of languages, battle-tested transport).

### Client-side multiplexing instead of server-side routing

Putting UUID routing logic entirely in clients (each language implements its own routing) was considered. Rejected because it increases per-language runtime complexity — the opposite of this JEP's goal. Server-side routing via `DriverRegistry` is implemented once and works for all client languages.

### Dynamic service generation from FileDescriptorProto

Using gRPC's dynamic dispatch (`DynamicMessage` in Java, descriptor pools in Python) to serve interfaces without pre-compiled stubs was considered. Rejected because dynamic dispatch loses compile-time type safety and IDE support — the primary benefits this JEP targets. Pre-compiled stubs from `protoc` are more efficient, better-tooled, and produce better error messages.

## Prior Art

- **gRPC service multiplexing** — Standard gRPC servers routinely host multiple services on a single port. This is the native design of gRPC and is used by major projects (Kubernetes API server, Envoy xDS, CockroachDB).

- **gRPC metadata for routing** — Using call metadata for routing context is a well-established pattern. Envoy's header-based routing, Istio's request routing, and Google Cloud's `x-goog-request-params` all use metadata to direct requests to specific backends.

- **Buf Connect** ([connectrpc.com](https://connectrpc.com/)) — Generates typed clients from `.proto` files for multiple languages. Jumpstarter's approach is analogous but for device interfaces rather than web APIs.

- **Kubernetes CRD controllers** — The Kubernetes API server multiplexes CRD-defined APIs on a single server. Each CRD generates typed Go clients via code generation from OpenAPI specs — analogous to how JEP-0011 protos generate typed stubs via `protoc`.

- **tonic (Rust gRPC)** — The tonic framework's `Router` supports composing multiple gRPC services on a single server, with per-service interceptors. This is the pattern Jumpstarter would adopt for Rust clients.

## Unresolved Questions

### Must resolve before acceptance

1. **Metadata key naming:** ~~Should the UUID metadata key be `x-jumpstarter-driver-uuid` (descriptive, follows `x-` convention) or `jumpstarter-driver-uuid` (shorter, `x-` prefix deprecated by RFC 6648)?~~ **Resolved:** `x-jumpstarter-driver-uuid` was chosen. The `x-` prefix provides clear namespacing and is consistent with existing gRPC metadata conventions (e.g., `x-goog-request-params`). The RFC 6648 deprecation applies to new IETF standards, not application-specific metadata.

2. **gRPC server resource overhead:** Does registering 10-20 native services on a single gRPC server create unacceptable memory or CPU overhead on resource-constrained exporters (e.g., Raspberry Pi)? Initial analysis suggests negligible overhead (gRPC services are just method routing tables), but this needs benchmarking.

3. **Proto evolution handling:** If an interface proto gains a new RPC method (e.g., `PowerInterface` adds `Reset`), the generated servicer must gracefully return `UNIMPLEMENTED` for the new method until the exporter is updated. Standard gRPC handles this natively (unimplemented methods return `UNIMPLEMENTED`), but the adapter generation pipeline needs to handle partial implementations cleanly.

### Can wait until implementation

4. **Client-streaming RPCs:** No current interface uses client-streaming RPCs, but the proto format allows them. Should the servicer generator support them from day one, or defer until an interface needs them?

5. **`@exportstream` evolution:** Should `@exportstream` methods eventually migrate to a native gRPC bidi-streaming pattern (eliminating `RouterService.Stream` for typed streams), or should `RouterService.Stream` remain the permanent transport for raw byte streams? This is a potential follow-up JEP.

6. **Interceptor composition:** Should the exporter support registering per-service gRPC interceptors (e.g., rate limiting on flash operations, logging on power operations)? This is a natural extension but adds complexity.

7. **Service versioning:** When multiple proto versions of an interface coexist (e.g., `power.v1` and `power.v2`), should the exporter serve both simultaneously? This interacts with JEP-0012's DriverInterface version tracking.

## Future Possibilities

The following are **not** part of this JEP but are natural extensions enabled by it:

- **Native `@exportstream` migration:** Replace `RouterService.Stream`-based byte streams with typed gRPC bidi-streaming services, eliminating the last use of the `DriverCall`-era infrastructure.

- **Per-method gRPC policies:** With native services, standard gRPC interceptors can enforce per-method deadlines, retry policies, rate limits, and circuit breakers — all configured declaratively without custom code.

- **gRPC-Web support:** Native services can be exposed through Envoy's gRPC-Web proxy, enabling browser-based dashboards to call driver interfaces directly (limited to unary and server-streaming RPCs).

- **API gateway integration:** Native gRPC services can be registered with API gateways (Kong, Envoy, Google Cloud Endpoints) for external access with authentication, rate limiting, and monitoring.

- **Out-of-process polyglot drivers:** Non-Python drivers (Kotlin, Rust, Go, etc.) could run as separate processes managed by the main exporter runtime. The exporter spawns the driver process, which serves its native gRPC interface on a local socket. The exporter proxies client calls to the driver process, aggregates `DriverInstanceReport` entries, and manages the driver lifecycle (health checks, restart on crash, graceful shutdown). This enables hardware drivers written in any language without requiring the driver to be compiled into the exporter binary — the exporter acts as a supervisor and gRPC multiplexer for a fleet of driver processes.

- **Exporter core rewrite in a systems language:** Once all drivers support the native gRPC interface and can run out-of-process, the exporter core itself (session management, router tunnel, driver supervision, gRPC multiplexing) could be rewritten in a faster systems language such as Rust or Go. The current Python exporter runtime would become just another driver host process, while the core handles connection management, routing, and lifecycle at native performance. This is far-future work that depends on full adoption of native gRPC interfaces and the out-of-process driver model.

## Implementation Phases

| Phase | Deliverable | Depends On | Status |
|-------|-------------|------------|--------|
| 1 | `DriverRegistry` + `ServicerAdapterInfo` registry + servicer adapter for PowerInterface | JEP-0011 ✅ | **done** |
| 2 | Exporter registers native services alongside `ExporterService`; `native_services` field in `DriverInstanceReport`; Go controller forwards field | Phase 1 | **done** |
| 3 | Python `AsyncDriverClient` transparently routes through native gRPC when available; `NativeClientAdapterInfo` registry; fallback to `DriverCall` for old exporters | Phase 2 | **done** |
| 4 | All bundled driver interfaces have generated servicer adapters | Phase 2 | planned |
| 5 | Java `DriverClient` transparently routes through native gRPC; generated typed clients for idiomatic access | Phase 2 | planned |
| 6 | Resource handle migration for flash/storage operations | Phase 4 | planned |
| 7 | `DriverCall` / `StreamingDriverCall` marked deprecated in proto | Phase 4, 5 | planned |

Priority order: **Python exporter-side first** (Phase 1-2, enables all clients), **then Python client transparent upgrade** (Phase 3, validates the pattern — existing client code unchanged), **then Java client transparent upgrade** (Phase 5, existing `DriverClient.call()` code benefits automatically), **then resource migration** (Phase 6, completes the picture).

## Implementation History

- 2026-04-11: JEP drafted
- 2026-04-11: Phase 1-3 implemented:
  - Added `native_services` field (field 7) to `DriverInstanceReport` proto
  - Implemented `DriverRegistry` with `ServicerAdapterInfo` global registry (replaces originally-proposed `_native_servicer_class` attribute approach)
  - Generated `PowerInterfaceServicer` adapter with sync/async driver support
  - Modified exporter `Session` with `_register_native_services()` and convention-based auto-import (`{package}.servicer`)
  - Implemented client-side `NativeClientAdapterInfo` registry and `_setup_native_services()` on `AsyncDriverClient`
  - `call_async()` and `streamingcall_async()` transparently route through native stubs with DriverCall fallback
  - Go controller forwards `NativeServices` field in Device CRD
  - 5 integration tests passing (on/off, streaming read, report verification, legacy fallback, gRPC reflection)

## References

- [JEP-0011: Protobuf Introspection and Interface Generation](./JEP-0011-protobuf-introspection-interface-generation.md)
- [JEP-0012: ExporterClass Mechanism](./JEP-0012-deviceclass-mechanism.md)
- [JEP-0014: Polyglot Typed Device Wrappers](./JEP-0014-polyglot-typed-device-wrappers.md)
- [gRPC Service Multiplexing](https://grpc.io/docs/guides/)
- [gRPC Metadata](https://grpc.io/docs/guides/metadata/)
- [gRPC Server Reflection](https://github.com/grpc/grpc/blob/master/doc/server-reflection.md)
- [Buf Connect](https://connectrpc.com/)
- [protoc Compiler](https://protobuf.dev/getting-started/)

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
