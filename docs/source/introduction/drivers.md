# Drivers

Jumpstarter uses a modular {term}`driver` model to build abstractions around the
interfaces used to interact with target devices, both physical hardware and
virtual systems.

An [{term}`Exporter`](exporters.md) uses drivers to "export" these interfaces from a
{term}`host` machine to the {term}`client`s via {term}`gRPC`. Drivers can be thought
of as a simplified API for an interface or {term}`device` type.

## Architecture

Drivers in Jumpstarter follow a client/server architecture where:

- Driver implementations run on the exporter side and interact directly with
  hardware or virtual devices
- Driver clients run on the client side and communicate with drivers via gRPC
- Interface classes define the contract between implementations and clients

The architecture follows a pattern with these key components:

- **Interface class** - An abstract base class using Python's ABCMeta to define
  the contract (methods and their signatures) that driver implementations must
  fulfill. The interface also specifies the client class through the `client()`
  class method.

- **Driver class** - Inherits from both the Interface and the base `Driver`
  class, implementing the logic to configure and use hardware interfaces. Driver
  methods are marked with the `@export` decorator to expose them over the
  network.

- **Driver client class** - Provides a user-friendly interface that can be used by
  clients to interact with the driver either locally or remotely over the
  network.

When a client requests a {term}`lease` and connects to an exporter, a {term}`session` is created
for all tests the client needs to execute. Within this session, the specified
`Driver` subclass is instantiated for each configured interface. These driver
instances live throughout the session's duration, maintaining state and
executing setup/teardown logic.

The session calls `reset()` on the root driver when it starts, and
`shutdown()` followed by `close()` when it ends; the base `Driver`
implementations pass each call on to the child drivers. Background work that
should run for as long as the session serves a driver belongs in the
driver's `serving()` async context manager. The session enters it for
every driver before it accepts calls. When it stops serving, it cancels the
work started there in a task group and waits for it to finish, before
`shutdown()` runs. Code after the `yield` runs cancelled, so cleanup that
awaits must shield itself. An exception raised before the `yield` makes the
session fail to start. If the background work fails later, the
session logs the error and keeps serving.

On the client side, a `DriverClient` subclass is instantiated for each exported
interface. Since clients may run on different machines than exporters,
`DriverClient` classes are loaded dynamically, and only when their class path
matches the client's driver allow list or the client allows all drivers. A
driver that is not allowed, or whose client package is not installed, gets a
stub client instead, which raises an error explaining why when the driver is
used.

To maintain compatibility, avoid making breaking changes to interfaces. Add new
methods when needed but preserve existing signatures. If breaking changes are
required, create new interface, client, and driver versions within the same
module.

Drivers are often used with [{term}`adapter`s](adapters.md), which transform driver
connections into different forms or interfaces for specific use cases.

## Types

The API reference of the documentation provides a complete list of all
standard drivers, you can find it here: [Driver API
Reference](../reference/package-apis/drivers/index.md).

Some categories of drivers include:

- [System Control](../reference/package-apis/drivers/index.md#system-control):
  Control power to devices, or general control.
- [Communication](../reference/package-apis/drivers/index.md#communication):
  Provide protocols for network communication, such as TCP/IP, Serial, CAN bus,
  etc.
- [Storage and Data](../reference/package-apis/drivers/index.md#storage-and-data):
  Control storage devices, such as SD cards or USB drives, and data.
- [Media](../reference/package-apis/drivers/index.md#media): Provide
  interfaces for media capture and playback, such as video or audio.
- [Automotive Diagnostics](../reference/package-apis/drivers/index.md#automotive-diagnostics):
  Provide automotive diagnostic protocol interfaces.
- [Flashing and Programming](../reference/package-apis/drivers/index.md#flashing-and-programming):
  Provide interfaces for flashing firmware and programming devices.
- [Emulation](../reference/package-apis/drivers/index.md#emulation):
  Manage virtual and emulated targets.
- [Test and Measurement](../reference/package-apis/drivers/index.md#test-and-measurement):
  Capture and decode signals, such as logic analyzer traces.
- [Utility](../reference/package-apis/drivers/index.md#utility): Provide
  utility functions, such as shell driver commands on an exporter.

### Composite Drivers

Composite drivers combine multiple lower-level drivers to create higher-level
abstractions or specialized workflows. For example, a composite driver might
coordinate power cycling, storage re-flashing, and serial communication to
automate a device initialization process.

In Jumpstarter, drivers are organized in a driver tree structure which allows for the
representation of complex device configurations that may be found in your
environment.

Here's an example of a composite driver tree:

```text
MyHarness         # Custom composite driver for the entire target device harness
+- TcpNetwork     # TCP Network driver to tunnel port 8000
+- MyPower        # Custom power driver to control device power
+- SDWire         # SD Wire storage emulator to enable re-flash on demand
+- DigitalOutput  # GPIO pin control to send signals to the device
+- MyDebugger     # Custom debugger interface composite driver
   +- PySerial    # Serial debugger with PySerial
```

## Configuration

Drivers are configured using a YAML {term}`exporter config` file, which specifies the
drivers to load and the parameters for each. Drivers are distributed as Python
packages making it easy to develop and install your own drivers.

Here is an example exporter config that loads drivers for both physical and
virtual devices:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: demo
endpoint: grpc.jumpstarter.example.com:443
token: xxxxx
export:
  # Physical hardware drivers
  power:
    type: jumpstarter_driver_yepkit.driver.Ykush
    config:
      serial: "YK25838"
      port: "1"

  serial:
    type: "jumpstarter_driver_pyserial.driver.PySerial"
    config:
      url: "/dev/ttyUSB0"
      baudrate: 115200

  # Virtual device drivers
  qemu:
    type: "jumpstarter_driver_qemu.driver.Qemu"
    config:
      mem: "1G"
      smp: 2
```

## Communication

Drivers expose their methods over gRPC using three RPC styles (see
[RPC life cycle](https://grpc.io/docs/what-is-grpc/core-concepts/#rpc-life-cycle)
for details on gRPC counterparts):

```{mermaid}
flowchart LR
    subgraph "Unary RPC"
        direction TB
        C1["Client"] -- "DriverCall\n(desired state)" --> D1["Driver"]
        D1 -- "Result" --> C1
        E1["Example: power on/off"]
    end

    subgraph "Server Streaming RPC"
        direction TB
        C2["Client"] -- "StreamingDriverCall\n(interval)" --> D2["Driver"]
        D2 -- "Result Stream" --> C2
        E2["Example: power readings"]
    end

    subgraph "Bidirectional Streaming RPC"
        direction TB
        C3["Client"] <-- "DriverStream\n(Byte Stream)" --> D3["Driver"]
        E3["Example: video capture"]
    end
```

- **Unary** - Methods marked with `@export` send a single request and receive a
  single response. Used for commands like power on/off or querying device state.
- **Server Streaming** - Methods marked with `@export` that return a generator
  produce a stream of responses from a single request. Used for continuous data
  like sensor readings.
- **Bidirectional Streaming** - Methods marked with the `@exportstream` decorator open a
  full-duplex byte stream. Used for serial communication, video capture, or
  tunneling existing protocols (such as SSH) over Jumpstarter.

## Authentication and Security

Driver access is controlled through Jumpstarter's authentication mechanisms:

### Local Mode Authentication

In {term}`local mode`, drivers are accessible to any process that can connect to the
local Unix socket. This is typically restricted by filesystem permissions. When
running tests locally, authentication is simplified since everything runs in the
same user context.

### Distributed Mode Authentication

In {term}`distributed mode`, authentication is handled through JWT tokens:

- **Client Authentication**: Clients authenticate to the {term}`controller` using JWT
  tokens, which establishes their identity and permissions
- **Exporter Authentication**: Similarly, exporters authenticate to the
  controller with their own tokens
- **Driver Access Control**: The controller enforces access control by only
  allowing authorized clients to acquire leases on exporters and their drivers
- **Driver allowlist**: {term}`Client config`s can specify which driver packages
  are allowed to be loaded, preventing unintended execution of untrusted code

### Driver Package Security

When using distributed mode, driver security considerations include:

- **Package Verification**: Clients can verify that only trusted driver packages
  are loaded by configuring allowlists
- **{term}`Access Policies <access policy>`**: [ExporterAccessPolicy](../reference/crds/exporteraccesspolicy.md)
  resources control which clients can lease which exporters, and for how long
- **Session Isolation**: Each lease gets its own session with new
  driver instances, so driver state does not carry over from one lease to the next

## Custom Drivers

While Jumpstarter comes with drivers for many basic interfaces, custom drivers
can be developed for specialized hardware interfaces, emulated environments, or
to provide domain-specific abstractions for your use case. Custom drivers follow
the same architecture pattern as built-in drivers and can be integrated into the
system through the exporter config.

## Example Implementation

```{testcode}
from sys import modules
from types import SimpleNamespace
from anyio import connect_tcp, sleep
from contextlib import asynccontextmanager
from collections.abc import Generator, AsyncGenerator
from abc import ABCMeta, abstractmethod
from jumpstarter.driver import Driver, export, exportstream
from jumpstarter.client import DriverClient
from jumpstarter.common.utils import serve

# Define an interface with ABCMeta
class GenericInterface(metaclass=ABCMeta):
    @classmethod
    def client(cls) -> str:
        return "example.GenericClient"

    @abstractmethod
    def query(self, param: str) -> str: ...

    @abstractmethod
    def get_data(self) -> Generator[dict, None, None]: ...

    @abstractmethod
    def create_stream(self): ...

# Implement the interface with the Driver base class
class GenericDriver(GenericInterface, Driver):
    @export
    def query(self, param: str) -> str:
        # This could be any device-specific command
        return f"Response for {param}"

    # driver calls can be either sync or async
    @export
    async def async_query(self, param: str) -> str:
        # Example of an async operation with delay
        await sleep(1)
        return f"Async response for {param}"

    @export
    def get_data(self) -> Generator[dict, None, None]:
        # Example of a streaming response - could be sensor data, logs, etc.
        for i in range(3):
            yield {"type": "data", "value": i, "timestamp": f"2023-04-0{i+1}"}

    # stream constructor has to be an AsyncContextManager
    # that yield an anyio.abc.ObjectStream
    @exportstream
    @asynccontextmanager
    async def create_stream(self):
        # This could be any stream connection to a device
        async with await connect_tcp(remote_host="example.com", remote_port=80) as stream:
            yield stream

class GenericClient(DriverClient):
    # client methods are sync
    def query(self, param: str) -> str:
        return self.call("query", param)

    def async_query(self, param: str) -> str:
        # async driver methods can be invoked the same way
        return self.call("async_query", param)

    def get_data(self) -> Generator[dict, None, None]:
        yield from self.streamingcall("get_data")

    # Streams can be used for bidirectional communication
    def with_stream(self, callback):
        with self.stream("create_stream") as stream:
            callback(stream)

modules["example"] = SimpleNamespace(GenericClient=GenericClient)

with serve(GenericDriver()) as client:
    assert client.query("test") == "Response for test"
    assert client.async_query("async test") == "Async response for async test"
    data = list(client.get_data())
    assert len(data) == 3
```
