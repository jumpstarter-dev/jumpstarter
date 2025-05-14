# Drivers

Jumpstarter uses a modular driver model to build abstractions around the
interfaces used to interact with target devices, both physical hardware and
virtual systems.

An [Exporter](exporters.md) uses Drivers to "export" these interfaces from a
host machine to the clients via [gRPC](https://grpc.io/). Drivers can be thought
of as a simplified API for an interface or device type.

## Architecture

Drivers in Jumpstarter follow a client/server architecture where:

- Driver implementations run on the exporter side and interact directly with
  hardware or virtual devices
- Driver clients run on the client side and communicate with drivers via gRPC
- Interface classes define the contract between implementations and clients

The architecture follows a pattern with these key components:

- **Interface Class** - An abstract base class using Python's ABCMeta to define
  the contract (methods and their signatures) that driver implementations must
  fulfill. The interface also specifies the client class through the `client()`
  class method.

- **Driver Class** - Inherits from both the Interface and the base `Driver`
  class, implementing the logic to configure and use hardware interfaces. Driver
  methods are marked with the `@export` decorator to expose them over the
  network.

- **Driver Client** - Provides a user-friendly interface that can be used by
  clients to interact with the driver either locally or remotely over the
  network.

When a client requests a lease and connects to an exporter, a session is created
for all tests the client needs to execute. Within this session, the specified
`Driver` subclass is instantiated for each configured interface. These driver
instances live throughout the session's duration, maintaining state and
executing setup/teardown logic.

On the client side, a `DriverClient` subclass is instantiated for each exported
interface. Since clients may run on different machines than exporters,
`DriverClient` classes are loaded dynamically when specified in the allowed
packages list.

To maintain compatibility, avoid making breaking changes to interfaces. Add new
methods when needed but preserve existing signatures. If breaking changes are
required, create new interface, client, and driver versions within the same
module.

Drivers are often used with [Adapters](adapters.md), which transform driver
connections into different forms or interfaces for specific use cases.

## Types

The API reference of the documentation provides a complete list of all standard
drivers, you can find it here: [Driver API
Reference](../reference/package-apis/drivers/index.md).

Some categories of drivers include:

- [System
  Control](../reference/package-apis/drivers/index.md#system-control-drivers):
  Control power to devices, or general control.
- [Communication](../reference/package-apis/drivers/index.md#communication-drivers):
  Provide protocols for network communication, such as TCP/IP, Serial, CAN bus,
  etc.
- [Data and
  Storage](../reference/package-apis/drivers/index.md#storage-and-data-drivers):
  Control storage devices, such as SD cards or USB drives, and data.
- [Media](../reference/package-apis/drivers/index.md#media-drivers): Provide
  interfaces for media capture and playback, such as video or audio.
- [Debug and
  Programming](../reference/package-apis/drivers/index.md#debug-and-programming-drivers):
  Provide interfaces for debugging and programming devices, such as JTAG or SWD,
  remote flashing, emulation, etc.
- [Utility](../reference/package-apis/drivers/index.md#utility-drivers): Provide
  utility functions, such as shell driver commands on a exporter.

### Composite Drivers

Composite drivers combine multiple lower-level drivers to create higher-level
abstractions or specialized workflows. For example, a composite driver might
coordinate power cycling, storage re-flashing, and serial communication to
automate a device initialization process.

In Jumpstarter, drivers are organized in a tree structure which allows for the
representation of complex device configurations that may be found in your
environment.

Here's an example of a composite driver tree:

```
MyHarness         # Custom composite driver for the entire target device harness
├─ TcpNetwork     # TCP Network driver to tunnel port 8000
├─ MyPower        # Custom power driver to control device power
├─ SDWire         # SD Wire storage emulator to enable re-flash on demand
├─ DigitalOutput  # GPIO pin control to send signals to the device
└─ MyDebugger     # Custom debugger interface composite driver
   └─ PySerial    # Serial debugger with PySerial
```

## Configuration

Drivers are configured using a YAML Exporter config file, which specifies the
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
    type: "jumpstarter_driver_qemu.driver.QEMU"
    config:
      image_path: "/var/lib/jumpstarter/images/vm.qcow2"
      memory: "1G"
      cpu_cores: 2
```

## Communication

Drivers use two primary methods to communicate between client and exporter:

### Messages

Commands are sent as messages from driver clients to driver implementations,
allowing the client to trigger actions or retrieve information from the device.
Methods marked with the `@export` decorator are made available over the network.

### Streams

Drivers can establish streams for continuous data exchange, such as for serial
communication or video streaming. This enables real-time interaction with both
physical and virtual interfaces across the network. Methods marked with the
`@exportstream` decorator create streams for bidirectional communication.


## Authentication and Security

Driver access is controlled through Jumpstarter's authentication mechanisms:

### Local Mode Authentication

In local mode, drivers are accessible to any process that can connect to the
local Unix socket. This is typically restricted by file system permissions. When
running tests locally, authentication is simplified since everything runs in the
same user context.

### Distributed Mode Authentication

In distributed mode, authentication is handled through JWT tokens:

- **Client Authentication**: Clients authenticate to the controller using JWT
  tokens, which establishes their identity and permissions
- **Exporter Authentication**: Similarly, exporters authenticate to the
  controller with their own tokens
- **Driver Access Control**: The controller enforces access control by only
  allowing authorized clients to acquire leases on exporters and their drivers
- **Driver Allowlist**: Client configurations can specify which driver packages
  are allowed to be loaded, preventing unintended execution of untrusted code

### Driver Package Security

When using distributed mode, driver security considerations include:

- **Package Verification**: Clients can verify that only trusted driver packages
  are loaded by configuring allowlists
- **Capability Restrictions**: Access to specific driver functionality can be
  restricted based on client permissions
- **Session Isolation**: Each client session operates with its own driver
  instances to prevent interference between users

## Custom Drivers

While Jumpstarter comes with drivers for many basic interfaces, custom drivers
can be developed for specialized hardware interfaces, emulated environments, or
to provide domain-specific abstractions for your use case. Custom drivers follow
the same architecture pattern as built-in drivers and can be integrated into the
system through the exporter configuration.

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