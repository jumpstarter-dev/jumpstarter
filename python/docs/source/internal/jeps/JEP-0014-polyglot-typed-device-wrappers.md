# JEP-0014: Polyglot Typed Device Wrappers

| Field             | Value                                                                                    |
| ----------------- | ---------------------------------------------------------------------------------------- |
| **JEP**           | 0014                                                                                     |
| **Title**         | Polyglot Typed Device Wrappers                                                           |
| **Author(s)**     | @kirkbrauer (Kirk Brauer)                                                                |
| **Status**        | Draft                                                                                    |
| **Type**          | Standards Track                                                                          |
| **Created**       | 2026-04-06                                                                               |
| **Updated**       | 2026-04-11                                                                               |
| **Discussion**    | [Matrix](https://matrix.to/#/#jumpstarter:matrix.org)                                    |
| **Requires**      | JEP-0011 (implemented), JEP-0012 (implemented), JEP-0013 (implemented)                  |
| **Supersedes**    | —                                                                                        |
| **Superseded-By** | —                                                                                        |

---

## Abstract

This JEP provides a code generation pipeline that produces type-safe device wrapper libraries in Python, Java, TypeScript, and Rust from the canonical `.proto` interface definitions (JEP-0011) and `ExporterClass` specifications (JEP-0012). A two-stage pipeline first uses standard `protoc` to generate language-specific message and service stubs — which, thanks to JEP-0013's native gRPC services, are directly usable as typed clients — then uses `jmp codegen` to compose those stubs into ExporterClass-typed device wrappers with named, non-nullable accessors for required interfaces and nullable accessors for optional ones.

Each target language requires a minimal runtime library (~200 lines) that handles session management (connecting to `JUMPSTARTER_HOST`), UUID metadata interception, and `@exportstream` forwarding. Non-Python clients initially operate under `jmp shell`, which handles lease acquisition, authentication, and channel setup — keeping per-language runtime requirements small. A Java runtime library already exists as an MVP on the `jumpstarter-java` branch, providing `ExporterSession` and driver discovery, which serves as the starting point for JVM-based codegen.

Alongside generated client types, the pipeline optionally generates test framework fixtures — JUnit 5 extensions, pytest base classes, Rust proc macros, and Jest/Vitest helpers — so that test code in any supported language gets IDE auto-complete, compile-time type checking, documentation, and zero-boilerplate device setup. Everything is generated from the same proto and ExporterClass definitions that the controller uses for validation.

## Motivation

With JEP-0013's native gRPC services now implemented, Jumpstarter's wire protocol is no longer opaque. Each driver interface (power, serial, network, etc.) is exposed as a standard gRPC service with fully-typed protobuf messages. Any language with a `protoc` plugin can generate type-safe client stubs automatically. The remaining challenge is not "implementing a wire protocol in every language" but rather composing those `protoc`-generated stubs into ergonomic device wrappers, providing session management for `jmp shell`, and integrating with each language's test framework.

JEP-0011 solves the schema problem: every interface now has a canonical `.proto` definition with full type information. JEP-0012 solves the contract problem: an `ExporterClass` declares exactly which interfaces a device provides, distinguishing required from optional. JEP-0013 solves the transport problem: native gRPC services mean standard `protoc` stubs work directly — no custom dispatch code needed. This JEP combines all three into a complete codegen pipeline that produces ready-to-use client libraries and test fixtures in multiple languages.

The immediate drivers for polyglot support are:

- **JVM-based test frameworks (Java/Kotlin).** Teams running hardware test suites through JVM-based frameworks (JUnit, TestNG, tradefed) today either shell out to Python scripts or assemble raw gRPC calls with proto boilerplate. A generated Java client with typed `device.power().on()` calls integrates natively with JUnit's test lifecycle and IDE tooling.

- **Web dashboards and MCP servers (TypeScript).** Jumpstarter's management UIs and MCP tool servers run in Node.js/TypeScript. A generated TypeScript client provides type-safe device access for dashboards and AI tool integrations without maintaining a hand-written client.

- **Performance-critical flash tooling (Rust).** High-throughput image flashing and storage operations benefit from Rust's zero-copy I/O and async runtime. A generated Rust client enables flash pipelines that match the performance of native tools while integrating with Jumpstarter's session and routing system.

- **Improved Python experience.** Even for existing Python users, the generated ExporterClass wrappers provide stronger typing than the current dynamic `client_from_channel` approach — `device.power` is a typed `PowerClient`, not a generic `DriverClient` discovered at runtime.

### User Stories

- **As a** JVM test engineer, **I want to** add a Gradle dependency for my ExporterClass and get a Java device wrapper with `power`, `serial`, `flash`, etc. as typed, auto-completing accessors, **so that** my JUnit tests call `device.power().on()` instead of assembling raw gRPC calls.

- **As a** web dashboard developer, **I want to** run `jmp codegen --language typescript --exporter-class dev-board` and get a TypeScript module I can import into my Next.js app, **so that** the dashboard can interact with leased devices with full type safety and IDE support.

- **As a** flash tooling developer, **I want to** generate a Rust client from the `storage-mux-flasher-v1` interface proto, **so that** my flash pipeline can write disk images at line speed through Jumpstarter's session system without Python overhead.

- **As a** CI infrastructure maintainer, **I want** the generated client libraries to be published as versioned packages (Maven, npm, crate, PyPI) tied to specific ExporterClass versions, **so that** test code pins to a known API and upgrades are explicit.

- **As a** JUnit test engineer, **I want** a `@JumpstarterDevice` annotation that handles `jmp shell` session setup and teardown, **so that** my test methods receive a typed device object ready to use without any boilerplate.

## Proposal

### `jmp shell`-First Approach

Non-Python clients initially operate exclusively under `jmp shell`, which:

1. Handles lease acquisition on the Python side
2. Sets the `JUMPSTARTER_HOST` environment variable pointing to a Unix socket or TCP address
3. Provides a fully-connected, authenticated gRPC channel to the exporter

This means per-language runtimes do **not** need to implement lease lifecycle (`RequestLease` → poll `GetLease` → `Dial`), controller client configuration, JWT authentication, mTLS setup, or client config management (`jmp config client`). All of that stays in Python. The non-Python runtime simply:

1. Reads `JUMPSTARTER_HOST` → creates an insecure gRPC channel
2. Calls `GetReport()` to discover the driver tree
3. Uses native gRPC stubs (from `protoc`) with a UUID metadata interceptor
4. Calls native gRPC bidi streams for `@exportstream` byte channels (using `StreamData` messages)

This keeps per-language runtimes at ~200 lines instead of ~1000+. Full standalone client packages with lease acquisition and controller integration are a future extension.

### Two-Stage Generation Pipeline

Code generation happens in two stages, each using the appropriate tool for the job:

```
                     ┌──────────────────┐
                     │   .proto files   │ ← from JEP-0011
                     │  (per interface) │
                     └────────┬─────────┘
                              │
                     ┌────────▼─────────┐
                     │    protoc        │ ← standard protobuf compiler
                     │  language stubs  │
                     └────────┬─────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
    ┌─────────▼─────┐  ┌──────▼──────┐ ┌──────▼──────┐
    │ Python stubs  │  │  Java stubs │ │  TS stubs   │ ...
    └─────────┬─────┘  └──────┬──────┘ └──────┬──────┘
              │               │               │
              └───────────────┼───────────────┘
                              │
                   ┌──────────▼──────────┐
                   │   ExporterClass     │ ← from JEP-0012
                   │   definition        │
                   └──────────┬──────────┘
                              │
                     ┌────────▼────────┐
                     │ jmp codegen     │ ← Jumpstarter-specific
                     │ (typed wrapper  │
                     │  + test fixture)│
                     └────────┬────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
    ┌─────────▼─────┐  ┌──────▼──────┐ ┌──────▼──────┐
    │ Python device │  │ Java device │ │ TS device   │
    │ wrapper +     │  │ wrapper +   │ │ wrapper +   │
    │ pytest fixture│  │ JUnit ext.  │ │ jest helper  │
    └───────────────┘  └─────────────┘ └─────────────┘
```

**Stage 1: `protoc` stubs (standard tooling).** The `.proto` files produced by JEP-0011's `jmp proto export` are fed into `protoc` with language-specific plugins (`protoc-gen-grpc-java`, `protoc-gen-ts`, `protoc-gen-prost`). This produces standard message classes and gRPC service stubs in each target language. Thanks to JEP-0013, these stubs are directly usable as native gRPC clients — they call the per-interface gRPC services that the exporter now registers alongside the legacy `ExporterService`. Proto comments from JEP-0011 flow through to produce language-native documentation (Javadoc, TSDoc, `///`, docstrings).

**Stage 2: `jmp codegen` wrapper (Jumpstarter-specific).** The Jumpstarter-specific `jmp codegen` tool reads an `ExporterClass` definition (JEP-0012), resolves its `DriverInterface` references, and generates:

1. **Per-interface typed client classes** that wrap the Stage 1 stubs with session management (channel from `ExporterSession`) and UUID metadata injection (routing calls to the correct driver instance via `x-jumpstarter-driver-uuid` header).
2. **An ExporterClass device wrapper** that composes the per-interface clients into a single device object with named accessors. Required interfaces become non-nullable; optional interfaces become nullable.
3. **Test framework fixtures** (optional, via `--test-fixtures`) that provide zero-boilerplate device setup for JUnit, pytest, Rust `#[test]`, and Jest/Vitest.

### Wire Protocol: Native gRPC (JEP-0013)

JEP-0013 is now fully implemented (Phases 1-3). Exporters register native gRPC services for each driver interface alongside the legacy `ExporterService`. The generated typed client wrappers in this JEP build directly on that foundation — each generated client (e.g., `PowerClient`) wraps the `protoc`-generated blocking or async stub for the corresponding native gRPC service (e.g., `PowerInterfaceGrpc.PowerInterfaceBlockingStub`), handling channel setup, UUID metadata injection, and proto message construction internally. Users interact with clean, idiomatic APIs (`power.on()`) rather than raw gRPC stubs.

With JEP-0013's native gRPC transport, the per-language runtime is minimal:

| Component | With `DriverCall` (legacy) | With native gRPC (JEP-0013) |
|-----------|---------------------------|------------------------------|
| Value serde | ~30 lines per language | **Eliminated** — standard protobuf |
| Driver dispatch | ~60 lines per language | **Eliminated** — standard `protoc` stubs |
| Lease lifecycle | ~50 lines per language | **Deferred** — `jmp shell` handles this |
| Stream forwarding | ~60 lines per language | ~60 lines (unchanged) |
| Resource adapter | ~80 lines per language | ~80 lines (unchanged) |
| UUID metadata interceptor | N/A | ~15 lines (standard gRPC interceptor) |
| Session management | N/A (part of lease) | ~40 lines (`JUMPSTARTER_HOST` → channel) |
| **Total** | **~280 lines** | **~195 lines** |

The eliminated components (Value serde, driver dispatch) were the most error-prone and hardest to test across languages. Standard `protoc` stubs are battle-tested in every target language.

`@exportstream` methods (JEP-0011) use native gRPC bidi streaming with `StreamData { bytes payload }` messages for byte transport.

### CLI Interface

```bash
# Generate interface stubs only (standard protoc)
jmp codegen stubs \
  --language java \
  --interfaces power-v1,serial-v1,network-v1 \
  --output src/gen/java/

# Generate ExporterClass wrapper (Jumpstarter-specific)
jmp codegen exporter-class \
  --language java \
  --exporter-class dev-board \
  --output src/gen/java/

# All-in-one: stubs + ExporterClass wrapper + test fixtures
jmp codegen \
  --language java \
  --exporter-class dev-board \
  --test-fixtures \
  --output src/gen/java/
```

The `--exporter-class` flag accepts either an ExporterClass name (resolved from the cluster or a local YAML file) or a `--exporter-class-file` path for offline codegen without cluster access.

The `--test-fixtures` flag generates framework-specific test helpers alongside the client code (JUnit 5 extension for Java, pytest base class for Python, proc macro for Rust, Jest helper for TypeScript).

### Generated Output Per Language

#### Python

JEP-0011's `jmp proto generate` already generates per-interface client classes (e.g., `PowerClient`). JEP-0014's Python codegen focuses on the ExporterClass-typed wrapper that composes multiple interface clients into a single device object, plus a pytest base class for testing:

```python
# generated: jumpstarter_gen/devices/dev_board.py
from jumpstarter.common.utils import env
from jumpstarter_driver_power.client import PowerClient
from jumpstarter_driver_opendal.client import StorageMuxFlasherClient as FlasherClient
from jumpstarter_driver_pyserial.client import SerialClient
from jumpstarter_driver_network.client import NetworkClient

class DevBoardDevice:
    """Auto-generated typed wrapper for ExporterClass dev-board.

    Do not edit — regenerate with `jmp codegen` when the ExporterClass changes.
    """
    power: PowerClient           # required — guaranteed by ExporterClass
    serial: SerialClient         # required
    flash: FlasherClient         # required
    network: NetworkClient | None  # optional — may be None

    def __init__(self, session):
        self.power = session.require_driver("power", PowerClient)
        self.serial = session.require_driver("serial", SerialClient)
        self.flash = session.require_driver("flash", FlasherClient)
        self.network = session.optional_driver("network", NetworkClient)
```

Test code uses it under `jmp shell` with full type safety:

```python
# Using jmp shell (JUMPSTARTER_HOST is set)
async with env() as client:
    device = DevBoardDevice(client)
    device.power.on()                          # typed, auto-complete works
    for reading in device.power.read():
        assert reading.voltage > 4.5           # typed field access
    with device.serial.stream("connect") as s: # @exportstream method
        s.send(b"AT\r\n")
```

#### Java

The generated Java client wraps `protoc`-generated gRPC stubs with session management and UUID metadata routing. The `ExporterSession` reads `JUMPSTARTER_HOST` and provides the gRPC channel; per-interface clients use standard blocking or async stubs with a UUID interceptor:

```java
// generated: dev/jumpstarter/devices/DevBoardDevice.java
package dev.jumpstarter.devices;

import dev.jumpstarter.client.ExporterSession;

public class DevBoardDevice implements AutoCloseable {
    /** Power control — required by ExporterClass dev-board */
    private final PowerClient power;
    /** Serial console — required */
    private final SerialClient serial;
    /** Storage mux flasher — required */
    private final FlasherClient flash;
    /** Network interface — optional, may be null */
    private final NetworkClient network;

    public DevBoardDevice(ExporterSession session) {
        this.power = new PowerClient(session, "power");
        this.serial = new SerialClient(session, "serial");
        this.flash = new FlasherClient(session, "flash");
        this.network = session.hasDriver("network") ? new NetworkClient(session, "network") : null;
    }

    public PowerClient power() { return power; }
    public SerialClient serial() { return serial; }
    public FlasherClient flash() { return flash; }
    public NetworkClient network() { return network; }

    @Override
    public void close() { /* session cleanup */ }
}
```

Each per-interface client (e.g., `PowerClient`) wraps the `protoc`-generated stub:

```java
// generated: dev/jumpstarter/interfaces/power/v1/PowerClient.java
package dev.jumpstarter.interfaces.power.v1;

import dev.jumpstarter.client.ExporterSession;
import dev.jumpstarter.client.UuidMetadataInterceptor;
import io.grpc.Channel;
import com.google.protobuf.Empty;
import java.util.Iterator;

public class PowerClient {
    private final PowerInterfaceGrpc.PowerInterfaceBlockingStub stub;

    public PowerClient(ExporterSession session, String driverName) {
        String uuid = session.getReport().findByName(driverName).uuid();
        Channel channel = session.getChannel();
        this.stub = PowerInterfaceGrpc.newBlockingStub(channel)
            .withInterceptors(new UuidMetadataInterceptor(uuid));
    }

    public void on() { stub.on(Empty.getDefaultInstance()); }
    public void off() { stub.off(Empty.getDefaultInstance()); }
    public Iterator<PowerReading> read() { return stub.read(Empty.getDefaultInstance()); }
}
```

Kotlin callers use the Java-generated classes directly with natural Kotlin syntax. Kotlin coroutine extensions (wrapping blocking stubs with `withContext(Dispatchers.IO)` or using grpc-kotlin async stubs) are a future enhancement.

#### TypeScript

Generates typed classes with async/await patterns:

```typescript
// generated: jumpstarter/devices/DevBoardDevice.ts
import { ExporterSession } from "@jumpstarter/client";
import { PowerClient } from "@jumpstarter/driver-power";
import { FlasherClient } from "@jumpstarter/driver-opendal";
import { SerialClient } from "@jumpstarter/driver-serial";
import { NetworkClient } from "@jumpstarter/driver-network";

export class DevBoardDevice {
  /** Power control — required */
  readonly power: PowerClient;
  /** Serial console — required */
  readonly serial: SerialClient;
  /** Storage mux flasher — required */
  readonly flash: FlasherClient;
  /** Network interface — optional */
  readonly network?: NetworkClient;

  constructor(session: ExporterSession) {
    this.power = session.requireDriver("power");
    this.serial = session.requireDriver("serial");
    this.flash = session.requireDriver("flash");
    this.network = session.optionalDriver("network");
  }
}
```

#### Rust

Generates strongly typed structs with lifetime management:

```rust
// generated: jumpstarter/devices/dev_board.rs
use jumpstarter_client::ExporterSession;
use jumpstarter_driver_power::PowerClient;
use jumpstarter_driver_opendal::FlasherClient;
use jumpstarter_driver_serial::SerialClient;
use jumpstarter_driver_network::NetworkClient;

/// Auto-generated typed wrapper for ExporterClass dev-board.
pub struct DevBoardDevice<'a> {
    /// Power control — required
    pub power: PowerClient<'a>,
    /// Serial console — required
    pub serial: SerialClient<'a>,
    /// Storage mux flasher — required
    pub flash: FlasherClient<'a>,
    /// Network interface — optional
    pub network: Option<NetworkClient<'a>>,
}
```

### Testing Primitives

A key deliverable of this JEP is test framework integration for each target language. The existing Python `JumpstarterTest` base class (in `jumpstarter-testing`) provides the pattern: it reads `JUMPSTARTER_HOST` for `jmp shell` mode, falls back to lease acquisition with a `selector`, and provides a `client` fixture scoped to the test class.

The `jmp codegen --test-fixtures` flag generates framework-specific test helpers alongside the client types. These fixtures handle session setup/teardown and provide typed device objects to test methods with zero boilerplate.

#### Python (pytest)

Extends the existing `JumpstarterTest` with a typed device fixture:

```python
# generated: jumpstarter_gen/testing/dev_board.py
from jumpstarter_testing.pytest import JumpstarterTest
from jumpstarter_gen.devices.dev_board import DevBoardDevice
import pytest

class DevBoardTest(JumpstarterTest):
    """Base class for tests targeting dev-board ExporterClass.

    Inherit from this class and use the `device` fixture for typed access.
    Supports both `jmp shell` (via JUMPSTARTER_HOST) and lease acquisition
    (via `selector` class variable).
    """
    selector = "jumpstarter.dev/exporter-class=dev-board"

    @pytest.fixture(scope="class")
    def device(self, client) -> DevBoardDevice:
        return DevBoardDevice(client)
```

Usage:

```python
class TestPowerCycle(DevBoardTest):
    def test_power_on(self, device: DevBoardDevice):
        device.power.on()
        readings = list(device.power.read())
        assert all(r.voltage > 4.5 for r in readings)
```

#### Java (JUnit 5)

A `JumpstarterExtension` reads `JUMPSTARTER_HOST`, creates an `ExporterSession`, builds the typed device wrapper, and injects it into test fields annotated with `@JumpstarterDevice`:

```java
// runtime: dev/jumpstarter/testing/JumpstarterExtension.java
// runtime: dev/jumpstarter/testing/JumpstarterDevice.java (annotation)

// generated: dev/jumpstarter/testing/DevBoardDeviceProvider.java
// (wires ExporterSession → DevBoardDevice construction)
```

Usage:

```java
@ExtendWith(JumpstarterExtension.class)
class PowerTest {
    @JumpstarterDevice
    DevBoardDevice device;

    @Test
    void powerOn() {
        device.power().on();
        Iterator<PowerReading> readings = device.power().read();
        while (readings.hasNext()) {
            PowerReading r = readings.next();
            assertTrue(r.getVoltage() > 4.5);
        }
    }
}
```

The extension handles `ExporterSession` lifecycle (create before all tests, close after all tests) and provides the gRPC channel to the generated device wrapper.

#### Rust

A `#[jumpstarter_test]` proc macro handles `JUMPSTARTER_HOST` session setup and provides the typed device as a test function argument:

```rust
use jumpstarter_testing::jumpstarter_test;
use jumpstarter_gen::devices::DevBoardDevice;

#[jumpstarter_test]
async fn test_power_on(device: DevBoardDevice<'_>) {
    device.power.on().await.unwrap();
    let mut readings = device.power.read().await.unwrap();
    while let Some(r) = readings.next().await {
        assert!(r.voltage > 4.5);
    }
}
```

#### TypeScript (Jest / Vitest)

A `createDevice()` factory reads `JUMPSTARTER_HOST` and returns a typed device with automatic cleanup:

```typescript
import { createDevice } from "@jumpstarter/dev-board/testing";
import { DevBoardDevice } from "@jumpstarter/dev-board";

describe("power", () => {
    const ctx = createDevice<DevBoardDevice>();

    test("power on", async () => {
        await ctx.device.power.on();
        for await (const reading of ctx.device.power.read()) {
            expect(reading.voltage).toBeGreaterThan(4.5);
        }
    });

    afterAll(() => ctx.close());
});
```

### Core Runtime Library Per Language

Each language needs a minimal runtime library alongside the generated code. With JEP-0013's native gRPC services, the runtime is significantly simpler than it would have been with `DriverCall` dispatch:

| Component              | What it does                                                                                                | Estimated size |
| ---------------------- | ----------------------------------------------------------------------------------------------------------- | -------------- |
| Session management     | Read `JUMPSTARTER_HOST`, create gRPC channel, call `GetReport()` for driver discovery                       | ~40 lines      |
| UUID metadata interceptor | Inject `x-jumpstarter-driver-uuid` gRPC metadata header for driver instance routing                     | ~15 lines      |
| Stream forwarding      | Native gRPC bidi stream bridging for `@exportstream` methods (TCP/UDP port forwarding)                       | ~60 lines      |
| Resource adapter       | Client-side data source/sink for flash/storage operations (see "Client-Side Logic Audit" in Design Details) | ~80 lines      |
| Test framework integration | JUnit extension, pytest fixture, Rust proc macro, Jest helper                                           | ~50 lines      |

Total estimated sizes:

| Language   | Estimated lines | Status                | Key dependencies                             |
| ---------- | --------------- | --------------------- | -------------------------------------------- |
| Java       | ~300            | MVP exists (needs update for native gRPC) | grpc-netty-shaded, protobuf-java, JUnit 5    |
| TypeScript | ~250            | planned               | @grpc/grpc-js, google-protobuf               |
| Rust       | ~280            | planned               | tonic, prost, tokio                           |
| Python     | existing        | **complete**          | grpcio, protobuf (no new runtime needed)      |

The Java MVP on the `jumpstarter-java` branch provides `ExporterSession` (connection management including Unix domain socket proxy for `jmp shell`), `DriverReport` (device tree traversal with label-based lookup), and `ValueCodec`/`DriverClient` (legacy `DriverCall` dispatch). The native gRPC update replaces `ValueCodec` and `DriverClient.call()` with standard `protoc`-generated stubs and a UUID metadata interceptor — a significant simplification.

The resource adapter is critical for practical utility: without it, generated clients can call control methods (`power.on()`, `serial.connect()`) but cannot perform data transfer operations (`flash.write()`, `storage.dump()`). Rust has a natural advantage here since opendal is a Rust-native project; Java and TypeScript may use simpler file-streaming adapters initially and add full opendal support later.

### Build System Integrations

**Gradle (JVM):**

```kotlin
// build.gradle.kts
plugins {
    id("dev.jumpstarter.codegen") version "1.0.0"
}

jumpstarter {
    exporterClass = "dev-board"
    exporterClassFile = file("exporter-classes/dev-board.yaml")
    generateTestFixtures = true
}
```

The Gradle plugin runs `jmp codegen` during the build's `generateSources` phase, producing Java source files that are compiled alongside user code.

**npm (TypeScript):**

```json
{
  "scripts": {
    "codegen": "jmp codegen --language typescript --exporter-class-file exporter-classes/dev-board.yaml --test-fixtures --output src/gen/"
  },
  "devDependencies": {
    "@jumpstarter/codegen": "^1.0.0"
  }
}
```

**Cargo (Rust):**

```toml
[build-dependencies]
jumpstarter-build = "1.0"
```

With a `build.rs` that invokes codegen during `cargo build`.

### API / Protocol Changes

No new protocol changes beyond those introduced by JEP-0013. This JEP consumes JEP-0013's native gRPC services and UUID metadata routing, JEP-0011's `.proto` files and `FileDescriptorProto` descriptors, and JEP-0012's ExporterClass CRDs — without modifying any of them. The `jmp codegen` CLI subcommand is new.

### Hardware Considerations

This JEP is purely a code generation and build tooling change. No hardware is required or affected. The generated clients interact with hardware through native gRPC services (JEP-0013), including native bidi streaming for `@exportstream` byte transport.

## Design Details

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   jmp codegen                           │
│                                                         │
│  ┌────────────────┐  ┌─────────────────┐  ┌───────────────┐ │
│  │ ExporterClass  │  │ DriverInterface │  │ .proto files  │ │
│  │ YAML/CRD       │  │ CRDs            │  │ (JEP-0011)    │ │
│  └───────┬────────┘  └───────┬─────────┘  └──────┬────────┘ │
│         │                 │                 │           │
│         ▼                 ▼                 ▼           │
│  ┌──────────────────────────────────────────────────┐   │
│  │              Code Generator Engine               │   │
│  │  - Resolve ExporterClass → DriverInterface refs   │   │
│  │  - Invoke protoc for Stage 1 stubs                │   │
│  │  - Emit ExporterClass wrapper for target language  │   │
│  │  - Emit test fixtures (if --test-fixtures)         │   │
│  └──────────────────────────────────────────────────┘   │
│         │                                               │
│         ▼                                               │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Generated output:                                │   │
│  │  - Per-interface client stubs (from protoc)       │   │
│  │  - Per-interface typed clients (from jmp codegen)  │   │
│  │  - ExporterClass wrapper (from jmp codegen)        │   │
│  │  - Test fixtures (from jmp codegen)                │   │
│  │  - Package metadata (pom.xml / package.json / ..) │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### Handling `@exportstream` Methods in Non-Python Languages

JEP-0011 defines `@exportstream` methods as raw byte stream constructors (e.g., `TcpNetwork.connect()`, `PySerial.connect()`) that produce bidirectional byte channels. These are represented as native gRPC bidi streaming RPCs with `StreamData { bytes payload }` messages.

In generated non-Python clients, `stream_constructor` methods produce a language-native byte stream object:

- **Java:** Returns a `StreamChannel` (custom class wrapping gRPC bidi stream) with `InputStream`/`OutputStream` accessors.
- **TypeScript:** Returns a Node.js `Duplex` stream or an `AsyncIterableIterator<Uint8Array>`.
- **Rust:** Returns a `(Sender<Bytes>, Receiver<Bytes>)` pair using `tokio::sync::mpsc`.

The generated code calls the native gRPC bidi endpoint directly (e.g., `NetworkInterface/Connect`), wrapping the `StreamData` stream in a language-idiomatic interface. No `RouterService.Stream` framing is needed — standard gRPC handles connection lifecycle.

### Client-Side Logic Audit: What Auto-Generation Covers and What It Doesn't

An audit of all core in-tree driver clients reveals four tiers of client-side logic. Understanding these tiers is essential for setting expectations about what `jmp codegen` can generate automatically and what requires per-language work.

#### Tier 1: Pure delegation — fully auto-generated

Clients where every method is a direct gRPC call to the native service. These are fully covered by auto-generation with zero manual code:

`StorageMuxClient` (`host()`, `dut()`, `off()`, `write()`, `read()`), `NetworkClient` (`connect()`, `address()`), and `CompositeClient` (no methods, just children). These represent the bulk of the standard interface surface.

#### Tier 2: Light client-side orchestration — move server-side or trivially port

Methods that compose multiple interface calls or add minor client-side logic:

`PowerClient.cycle(wait=2)` calls `off()`, sleeps for `wait` seconds, then calls `on()`. This is three lines of client-side orchestration that would need to be reimplemented in every target language. **Recommendation:** move `cycle()` to the exporter as an `@export` method so it becomes a single RPC. Some drivers (e.g., RideSX) already implement `cycle()` server-side. This should be standardized across all power drivers before polyglot codegen ships.

`PowerClient.read()` wraps streaming results in `PowerReading` — with JEP-0013's native gRPC, the proto message class's native deserialization handles this in each language. Auto-generation handles this.

`PowerClient.rescue()` calls a method not part of `PowerInterface`. It's a DutLink-specific extension. **Recommendation:** either add `rescue()` to the standard `PowerInterface` or document it as a driver-specific extension that won't appear in auto-generated clients for the base power interface.

#### Tier 3: Resource mechanism — requires per-language runtime support

The `FlasherClient` and `StorageMuxFlasherClient` use Jumpstarter's **resource mechanism**, which is the most significant barrier to polyglot support. The pattern works like this:

1. The client resolves a file path to a data source using `opendal` (handles local files, HTTP URLs, S3, OCI registries).
2. The client creates an `OpendalAdapter` which registers a **client-side resource** — a streaming handle that the exporter can read from or write to.
3. The client passes the resource handle to the exporter via the native gRPC call.
4. The exporter opens a reverse stream to the client's resource and transfers the data.

This is not a simple RPC delegation — it involves client-side I/O, streaming, and the resource registration protocol. The affected methods are:

- `FlasherClient.flash(path, target, operator, compression)` — resolves path → creates `OpendalAdapter(mode="rb")` → calls flash RPC with handle
- `FlasherClient.dump(path, target, operator, compression)` — resolves path → creates `OpendalAdapter(mode="wb")` → calls dump RPC with handle
- `StorageMuxFlasherClient.flash()` — calls `host()` → creates `OpendalAdapter` → calls `write(handle)` → calls `dut()`
- `StorageMuxFlasherClient.dump()` — calls `host()` → creates `OpendalAdapter` → calls `read(handle)` → calls `dut()`

For polyglot clients to support flash and storage operations, each language's runtime library needs a **resource adapter** component that can register client-side data sources/sinks and pass handles to the exporter. This is approximately 60–100 additional lines per language, and requires either per-language opendal bindings (Rust has native opendal; Java and TypeScript would need JNI/NAPI bindings or a reimplementation) or a simpler file-streaming adapter that handles the most common case (local files and HTTP URLs).

The resource adapter is the single most important runtime component for making polyglot clients practically useful — without it, generated clients can call `power.on()` and `serial.connect()` but can't flash devices.

#### Tier 4: Complex orchestration — out of scope for auto-generation

`BaseFlasherClient` (in `jumpstarter-driver-flashers`) is an entire flash orchestration framework with hundreds of lines of Python-specific logic: serial console interaction via `pexpect`, U-Boot bootloader control, retry logic with exponential backoff, manifest parsing, busybox shell sessions, redacting console writers, and file upload with hash deduplication. It imports `pexpect`, `requests`, `threading`, and `queue`.

**This is explicitly out of scope for auto-generation.** `BaseFlasherClient` is a Python-only orchestration layer that will not be ported to other languages through codegen. Teams needing equivalent functionality in Java or TypeScript would reimplement the orchestration logic in their language, calling the generated typed clients for the underlying interface methods (`power.on()`, `serial.connect()`, `flash.write()`). Over time, moving more of this orchestration server-side (onto the exporter) reduces the per-language reimplementation burden.

Similarly, device-specific composite clients like `RideSXClient` (fastboot detection, multi-partition orchestration, OCI registry handling) contain substantial business logic that is specific to a hardware platform and not derivable from the proto definition. These remain hand-written.

### Error Handling

- **Missing required interface at runtime:** If `session.requireDriver("power")` fails because the exporter's report doesn't contain a matching driver, the runtime throws a descriptive error naming the missing interface and the ExporterClass. This should not happen if the controller correctly validates the ExporterClass at lease time — it's a defense-in-depth check.
- **Proto version mismatch:** With native gRPC, proto version mismatches between client stubs and server implementations are handled by standard protobuf wire compatibility rules. Structural compatibility checking (JEP-0012) catches breaking changes at registration time.
- **`protoc` not installed:** `jmp codegen stubs` requires `protoc` to be available. If it's missing, the CLI provides an actionable error with installation instructions for the user's platform.

### Security Implications

The generated clients inherit the security model of the Jumpstarter Python client. Under `jmp shell`, the gRPC channel is pre-authenticated — the Python side handles JWT/mTLS, and the non-Python client connects to the local `JUMPSTARTER_HOST` socket. No additional attack surface is introduced. The generated code does not embed credentials; authentication tokens are provided at runtime through `jmp shell`'s environment setup.

## Test Plan

### Unit Tests

- **ExporterClass resolution:** Verify that the code generator correctly resolves an ExporterClass's `DriverInterface` references and produces the correct set of accessor fields (required as non-nullable, optional as nullable) for each target language.
- **Proto comment propagation:** Verify that doc comments from `.proto` files appear as language-native documentation in generated stubs (Javadoc, TSDoc, `///`, docstrings).
- **`@exportstream` handling:** Verify that bidi streaming methods with `StreamData` generate native gRPC bidi calls with proper port-forward bridging in every target language.
- **Naming conventions:** Verify that proto `snake_case` method names are converted to language-idiomatic conventions (camelCase for Java/TypeScript, snake_case for Python/Rust).

### Integration Tests

- **Java end-to-end:** Generate a Java client from the `dev-board` ExporterClass, compile it with Gradle, run under `jmp shell` against a mock exporter, and verify that `device.power().on()`, `device.power().read()`, and `device.network().connectTcp()` produce correct native gRPC RPCs (`PowerInterface/On`, `PowerInterface/Read`, `NetworkInterface/Connect` bidi).
- **TypeScript end-to-end:** Generate a TypeScript client, compile with `tsc`, run under `jmp shell` against a mock exporter, and verify typed method calls produce correct native gRPC RPCs.
- **Python ExporterClass wrapper:** Generate a Python `DevBoardDevice`, verify it correctly wraps the existing Python interface clients with typed accessors.
- **Cross-language interop:** Run the same exporter under `jmp shell`, invoke it from both a Python client and a Java client, verify both can call the same driver methods and receive identical results.
- **Resource adapter (flash/storage):** Generate a Java client for `StorageMuxFlasherClient`, flash a local file to a mock exporter using the Java resource adapter, and verify the data arrives correctly. Repeat for TypeScript.

### Test Fixture Tests

- **JUnit extension:** Verify that `@JumpstarterDevice` annotation correctly injects a typed `DevBoardDevice` into a JUnit 5 test class when `JUMPSTARTER_HOST` is set.
- **pytest base class:** Verify that `DevBoardTest` subclass provides a typed `device` fixture connected to the exporter.
- **Rust proc macro:** Verify that `#[jumpstarter_test]` correctly creates a session and provides the typed device argument.
- **Jest helper:** Verify that `createDevice()` correctly connects and provides a typed device object.

### Hardware-in-the-Loop Tests

No HiL tests are required for the codegen tooling itself. The generated clients use JEP-0013's native gRPC services, which have their own HiL test coverage. Once a generated client passes integration tests against a mock exporter under `jmp shell`, it will work against real hardware through the same gRPC channel.

### Manual Verification

- Generate clients for all four languages from the `dev-board` ExporterClass and visually inspect the generated code for idiomatic style, correct documentation, and correct nullability annotations.
- Import the generated Java client into IntelliJ IDEA and verify IDE auto-complete, type checking, and Javadoc rendering.
- Import the generated TypeScript client into VS Code and verify IntelliSense, type checking, and TSDoc rendering.

## Graduation Criteria

### Experimental

- The Java runtime supports native gRPC via `ExporterSession` + UUID metadata interceptor + native bidi streaming for `@exportstream`.
- `jmp codegen` produces compilable Java and Python clients from at least one ExporterClass.
- JUnit 5 `JumpstarterExtension` injects typed device objects into test methods when running under `jmp shell`.
- Python `DevBoardTest` base class works with existing `JumpstarterTest` infrastructure.
- At least one real test (not just a mock) runs against a Jumpstarter exporter using a generated Java client under `jmp shell`.
- Generated clients include documentation from proto comments.
- `PowerClient.cycle()` has been moved server-side across all standard power drivers (prerequisite for clean auto-generation).

### Stable

- All four target languages (Python, Java, TypeScript, Rust) have working codegen, runtime libraries, and test framework integration.
- The resource adapter component supports at least local file streaming in Java and TypeScript, enabling flash/storage operations from non-Python clients.
- Build system plugins are published (Gradle, npm, Cargo).
- Testing primitives are functional for all four languages (JUnit, pytest, `#[jumpstarter_test]`, Jest).
- Generated client packages are published to their respective registries with version tracking tied to ExporterClass/DriverInterface versions.
- Cross-language interop is tested in CI, including at least one flash operation from a non-Python client.

## Backward Compatibility

This JEP is **fully backward compatible.** It introduces new tooling and generated code without modifying any existing components:

- The existing Python client is unchanged. The generated Python ExporterClass wrapper is a new layer on top of the existing interface clients — it doesn't replace them.
- The generated clients use native gRPC services (JEP-0013) for all driver communication, including native bidi streaming for `@exportstream` byte channels. The existing `ExporterService` and `DriverCall` RPC remain available for legacy clients.
- No operator-side changes. The generated clients connect to existing exporters through the existing controller and router infrastructure.
- The `jmp codegen` command is a new CLI subcommand that doesn't affect existing commands.

## Rejected Alternatives

### `DriverCall` wrappers instead of native gRPC stubs

An earlier draft of this JEP proposed generating typed wrappers that delegate to `DriverCall` with `google.protobuf.Value` arguments — essentially automating what the Python client does today. This was rejected because JEP-0013's native gRPC services eliminate the need for `DriverCall` dispatch and `ValueCodec` in generated clients. Standard `protoc` stubs are battle-tested in every target language and require no custom serialization code. Using native gRPC also enables per-method metrics and tracing (`/PowerInterface/On` instead of generic `/ExporterService/DriverCall`).

### gRPC-Web for browser clients instead of TypeScript codegen

gRPC-Web was considered for browser-based dashboards. This was rejected because gRPC-Web requires a proxy (Envoy) and doesn't support bidirectional streaming — which `@exportstream` methods (serial, TCP) require. The TypeScript codegen targets Node.js with `@grpc/grpc-js`, which supports full gRPC including bidi streaming. Browser support can be added later as a separate transport layer.

### Hand-writing clients per language instead of codegen

Manual client implementations were considered for the initial Java target. This was rejected because hand-written clients drift from the Python implementation, require per-language maintenance, and don't benefit from proto schema changes automatically. Codegen ensures all languages stay in sync with the canonical proto definitions.

### Single monolithic runtime instead of per-language packages

A single cross-language runtime using gRPC's polyglot support was considered. This was rejected because each language has its own idioms for async I/O, streaming, error handling, and package management. A thin per-language runtime (~200 lines) produces more natural code than a one-size-fits-all approach.

### Full standalone clients for initial release

Building full client packages with lease acquisition, controller authentication, and config management in every language was considered. This was rejected in favor of the `jmp shell`-first approach: `jmp shell` handles all the complex lifecycle management (lease, auth, config) on the Python side, and non-Python clients just need to connect to the `JUMPSTARTER_HOST` socket. This keeps per-language runtimes at ~200 lines instead of ~1000+ and delivers value faster. Full standalone clients are a future extension.

## Prior Art

- **gRPC codegen ecosystem** — `protoc` with language-specific plugins (`protoc-gen-go`, `protoc-gen-grpc-java`, `protoc-gen-ts`) is the standard approach for generating typed clients from `.proto` files. This JEP adds a Jumpstarter-specific composition layer on top of standard `protoc` output.

- **Buf Connect** ([connectrpc.com](https://connectrpc.com/)) — Buf's Connect framework generates typed clients from `.proto` files for multiple languages with pluggable transports. Jumpstarter's approach is similar in spirit — layering ergonomic, device-oriented APIs over proto-generated gRPC stubs.

- **OpenAPI Generator** ([openapi-generator.tech](https://openapi-generator.tech/)) — generates typed REST clients from OpenAPI specs in 50+ languages. Jumpstarter's `jmp codegen` serves an analogous purpose for gRPC-based HiL device APIs.

- **Kubernetes client generators** — The Kubernetes project generates typed clients for Go, Java, Python, and other languages from the OpenAPI spec of the Kubernetes API. The ExporterClass-typed wrapper is analogous to a generated Kubernetes resource client.

- **JUnit 5 extensions** — JUnit 5's `@ExtendWith` mechanism is the standard pattern for test lifecycle management in the JVM ecosystem. Spring's `@SpringBootTest`, Testcontainers' `@Testcontainers`, and Quarkus's `@QuarkusTest` all use this pattern to inject test infrastructure. The `JumpstarterExtension` follows the same design.

## Unresolved Questions

### Must resolve before acceptance

1. **`protoc` distribution:** Should `jmp codegen` bundle `protoc` or require it as an external dependency? Bundling simplifies setup; requiring it avoids version conflicts. Buf's `buf generate` bundles its own compiler — should Jumpstarter follow that pattern?

2. **Package naming convention:** What package names should the generated artifacts use? Proposal: `dev.jumpstarter:driver-power` (Maven), `@jumpstarter/driver-power` (npm), `jumpstarter-driver-power` (crate). Need to confirm these don't conflict with existing packages.

3. **ExporterClass resolution for offline codegen:** *(Partially resolved)* The JEP-0012 PoC implements both cluster-based resolution (via `jmp admin get exporterclasses` and `jmp admin get driverinterfaces`) and local YAML file support. The `--exporter-class-file` approach for offline use is validated by the admin CLI. Remaining question: should `jmp codegen` reuse the admin CLI's resolution logic directly, or implement its own lightweight resolver?

### Can wait until implementation

4. **Streaming return type ergonomics:** For native gRPC server-streaming methods like `power.read()`, what should the return type be in each language? Java `Iterator<PowerReading>`, TypeScript `AsyncIterable<PowerReading>`, Rust `Stream<PowerReading>`? The language-idiomatic choice differs per ecosystem.

5. **Error type mapping:** Should generated clients define language-specific exception types (e.g., `DeviceNotFoundError`, `InterfaceMismatchError`) or use the language's standard gRPC error types?

6. **Versioned package publication:** Should each ExporterClass version produce a separate package version? E.g., `dev.jumpstarter:my-device:1.0.0` maps to an ExporterClass with `interface_version` `1.0.0` from JEP-0011.

7. **Resource adapter scope per language:** Should the initial resource adapter in Java/TypeScript support only local file streaming (simplest, covers the most common flash use case), or should it support the full opendal operator set (HTTP, S3, OCI) from day one? Local-file-only is ~40 lines; full opendal support requires JNI/NAPI bindings to the Rust opendal library or a reimplementation of the operator resolution logic.

8. **`PowerClient.cycle()` migration path:** Moving `cycle()` server-side requires updating all power driver implementations to add `@export def cycle(self, wait)`. Should this be a coordinated change before JEP-0014 ships, or should generated clients include a fallback `cycle()` that calls `off()` + `sleep()` + `on()` client-side when the server doesn't support the method?

9. **Convenience method layering convention:** For methods like `cycle()` that exist on the current `PowerClient` but not in `PowerInterface`, should the generated client include them as client-side helpers, or should it strictly generate only interface methods? A strict approach keeps the generated code clean; a permissive approach avoids breaking existing Python users who expect `client.power.cycle()` to work.

10. **Test fixture generation scope:** Should `jmp codegen` always generate test fixtures alongside client code, or should test fixture generation be opt-in via `--test-fixtures`? Should the generated fixtures support both annotation-based injection (JUnit `@JumpstarterDevice`) and constructor-based setup, or just one approach per language?

## Future Possibilities

The following are **not** part of this JEP but are natural extensions enabled by it:

- **Full standalone client packages.** Non-Python languages could implement lease acquisition, controller authentication, logging, and config management to operate independently of `jmp shell`. This would enable pure Java/TypeScript/Rust test suites without Python as a dependency.

- **Driver Registry as codegen source (JEP-0005).** Once the Driver Registry catalogs DriverInterfaces and ExporterClasses, `jmp codegen` can resolve all inputs from the registry without requiring cluster access or local YAML files.

- **IDE plugins.** Language-specific IDE plugins (IntelliJ for Java, VS Code for TypeScript) that auto-generate or update device wrappers when ExporterClass definitions change.

- **Pre-built package registry.** A hosted registry of pre-generated client packages for all published ExporterClasses and DriverInterfaces, so consumers can `gradle dependency` / `npm install` without running codegen themselves.

- **Kotlin coroutine extensions.** Wrapping Java blocking stubs with `suspend` functions and `Flow<T>` for idiomatic Kotlin async usage.

- **Browser TypeScript clients via gRPC-Web.** Limited to unary and server-streaming RPCs (no bidi streaming for `@exportstream`), but sufficient for web dashboards that only need control operations.

## Implementation Phases

| Phase | Deliverable                                                                                                   | Depends On                 | Status       |
| ----- | ------------------------------------------------------------------------------------------------------------- | -------------------------- | ------------ |
| 0     | **Prerequisite:** Move `PowerClient.cycle()` server-side across all standard power drivers                    | —                          | planned      |
| 1     | Merge `jumpstarter-java` branch; update Java runtime for native gRPC (`ExporterSession` + UUID interceptor)   | JEP-0013 (done)            | planned      |
| 2     | `jmp codegen` CLI — reads ExporterClass + DriverInterface, invokes `protoc`, emits Java typed wrappers        | Phase 1                    | planned      |
| 3     | Java testing: JUnit 5 `JumpstarterExtension` + `@JumpstarterDevice` annotation                               | Phase 2                    | planned      |
| 4     | Python ExporterClass wrapper codegen + pytest `DevBoardTest` base class                                | Phase 2                    | planned      |
| 5     | TypeScript runtime + `jmp codegen --language typescript` + Jest/Vitest helpers                                | Phase 2                    | planned      |
| 6     | Rust runtime + `jmp codegen --language rust` + `#[jumpstarter_test]` proc macro                               | Phase 2                    | planned      |
| 7     | Build system plugins (Gradle, npm, Cargo) + package publishing                                                | Phases 3–6                 | planned      |

Phase 0 is a prerequisite that cleans up the interface/client boundary: methods like `cycle()` that are currently client-side orchestration become server-side `@export` methods, making them available through auto-generated clients in every language without per-language reimplementation.

Phase 1 merges the existing `jumpstarter-java` branch (which provides `ExporterSession`, `DriverReport`, `ValueCodec`, and `DriverClient` with Gradle build and tests) and updates it for JEP-0013: adding a `UuidMetadataInterceptor`, a method to create per-driver native gRPC stubs from the session's channel, and deprecating `ValueCodec` and `DriverClient.call()` in favor of protoc-generated stubs.

Priority order: **Java first** (JVM test framework use cases drive immediate demand), **then Python** (developer experience improvement for the existing ecosystem), **then TypeScript** (web dashboard and MCP server), **then Rust** (performance-critical flash tooling — benefits most from native opendal integration).

## Implementation History

- 2026-04-06: JEP drafted
- 2026-04-07: JEP-0011 PoC complete (descriptor builder, CLI `jmp interface generate`, proto files for bundled drivers)
- 2026-04-07: Java client MVP implemented (`ExporterSession`, `DriverClient`, `ValueCodec`, `DriverReport` with Gradle build, unit tests, integration tests)
- 2026-04-10: JEP-0012 PoC complete (ExporterClass and DriverInterface CRDs, Go controller validation, Python admin CLI)
- 2026-04-11: JEP-0013 Phases 1-3 implemented (native gRPC services, exporter registration, Python client transparent routing)
- 2026-04-11: JEP-0014 revised — simplified to `jmp shell`-first approach with native gRPC (JEP-0013) as foundation; added testing primitives as core deliverable; removed `DriverCall`/`ValueCodec` as required runtime components; changed primary JVM target from Kotlin to Java; added rejected alternative for `DriverCall` wrappers and full standalone clients

## References

- [JEP-0011: Protobuf Introspection and Interface Generation](./JEP-0011-protobuf-introspection-interface-generation.md)
- [JEP-0012: ExporterClass Mechanism](./JEP-0012-deviceclass-mechanism.md)
- [JEP-0013: Native gRPC Services for Driver Interfaces](./JEP-0013-native-grpc-services.md)
- [gRPC Code Generation](https://grpc.io/docs/languages/)
- [Buf Connect](https://connectrpc.com/)
- [OpenAPI Generator](https://openapi-generator.tech/)
- [protoc Compiler](https://protobuf.dev/getting-started/)
- [grpc-java](https://github.com/grpc/grpc-java)
- [tonic (Rust gRPC)](https://github.com/hyperium/tonic)
- [@grpc/grpc-js (Node.js)](https://www.npmjs.com/package/@grpc/grpc-js)
- [JUnit 5 Extensions](https://junit.org/junit5/docs/current/user-guide/#extensions)

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
