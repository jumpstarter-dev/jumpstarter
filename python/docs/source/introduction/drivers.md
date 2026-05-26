# Drivers

Jumpstarter uses a modular driver model to build abstractions around the
interfaces used to interact with target devices, both physical hardware and
virtual systems.

An [{term}`Exporter`](exporters.md) uses Drivers to "export" these interfaces from a
{term}`host` machine to the clients via {term}`gRPC`. Drivers can be thought
of as a simplified API for an interface or device type.

## Architecture

Drivers in Jumpstarter follow a client/server architecture where:

- Driver implementations run on the {term}`exporter` side and interact directly with
  hardware or virtual {term}`device`s
- Driver clients run on the client side and communicate with drivers via {term}`gRPC`
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

When a client requests a {term}`lease` and connects to an {term}`exporter`, a {term}`session` is created
for all tests the client needs to execute. Within this {term}`session`, the specified
`Driver` subclass is instantiated for each configured interface. These driver
instances live throughout the {term}`session`'s duration, maintaining state and
executing setup/teardown logic.

On the client side, a `DriverClient` subclass is instantiated for each exported
interface. Since clients may run on different machines than {term}`exporter`s,
`DriverClient` classes are loaded dynamically when specified in the allowed
packages list.

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
- [Utility](../reference/package-apis/drivers/index.md#utility): Provide
  utility functions, such as shell driver commands on an {term}`exporter`.

### Composite Drivers

Composite drivers combine multiple lower-level drivers to create higher-level
abstractions or specialized workflows. For example, a composite driver might
coordinate power cycling, storage re-flashing, and serial communication to
automate a device initialization process.

In Jumpstarter, drivers are organized in a driver tree structure which allows for the
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

Drivers are configured using a YAML exporter config file, which specifies the
drivers to load and the parameters for each. Drivers are distributed as Python
packages making it easy to develop and install your own drivers.

Here is an example exporter config that loads drivers for both physical and
virtual devices:

```{literalinclude} ../examples/introduction/driver_exporter_config.yaml
:language: yaml
```

## Communication

Drivers expose their methods over {term}`gRPC` using three RPC styles (see
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
local Unix socket. This is typically restricted by file system permissions. When
running tests locally, authentication is simplified since everything runs in the
same user context.

### Distributed Mode Authentication

In {term}`distributed mode`, authentication is handled through JWT tokens:

- **Client Authentication**: Clients authenticate to the {term}`controller` using JWT
  tokens, which establishes their identity and permissions
- **Exporter Authentication**: Similarly, {term}`exporter`s authenticate to the
  {term}`controller` with their own tokens
- **Driver Access Control**: The {term}`controller` enforces access control by only
  allowing authorized clients to acquire {term}`lease`s on {term}`exporter`s and their drivers
- **Driver allowlist**: Client configurations can specify which driver packages
  are allowed to be loaded, preventing unintended execution of untrusted code

### Driver Package Security

When using {term}`distributed mode`, driver security considerations include:

- **Package Verification**: Clients can verify that only trusted driver packages
  are loaded by configuring allowlists
- **Capability Restrictions**: Access to specific driver functionality can be
  restricted based on client permissions
- **{term}`Session` Isolation**: Each client {term}`session` operates with its own driver
  instances to prevent interference between users

## Custom Drivers

While Jumpstarter comes with drivers for many basic interfaces, custom drivers
can be developed for specialized hardware interfaces, emulated environments, or
to provide domain-specific abstractions for your use case. Custom drivers follow
the same architecture pattern as built-in drivers and can be integrated into the
system through the exporter configuration.

## Example Implementation

```{literalinclude} ../examples/introduction/driver_example.py
:language: python
```
