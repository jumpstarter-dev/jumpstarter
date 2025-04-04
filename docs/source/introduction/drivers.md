# Drivers

Jumpstarter uses a modular driver model to build abstractions around the
interfaces used to interact with target devices, both physical hardware and
virtual systems.

An [Exporter](./exporters.md) uses Drivers to "export" these interfaces from a
host machine to the clients via [gRPC](https://grpc.io/). Drivers can be thought
of as a simplified API for an interface or device type.

## Driver Architecture

Drivers in Jumpstarter follow a client/server architecture where:

- Driver implementations run on the exporter side and interact directly with
  hardware or virtual devices
- Driver clients run on the client side and communicate with drivers via gRPC
- Interface classes define the contract between implementations and clients

For comprehensive documentation on the driver architecture, including detailed
patterns and examples, see the [Driver Classes and
Architecture](../api-reference/drivers.md) reference.

## Types of Drivers

Jumpstarter includes several types of drivers organized by their primary
function:

### System Control Drivers

Drivers that control the power state and basic operation of devices:

- **Power Control** (`jumpstarter-driver-power`) - Control power to physical
  devices
- **Yepkit** (`jumpstarter-driver-yepkit`) - Control Yepkit USB switching
  devices
- **QEMU** (`jumpstarter-driver-qemu`) - Control virtual machines
  (start/stop/manage)
- **RaspberryPi** (`jumpstarter-driver-raspberrypi`) - Interface with Raspberry
  Pi GPIO

### Communication Drivers

Drivers that provide various communication interfaces:

- **Serial** (`jumpstarter-driver-pyserial`) - Interact with serial ports
- **CAN** (`jumpstarter-driver-can`) - Interface with CAN bus networks
- **Network** (`jumpstarter-driver-network`) - Tunnel TCP/UDP network traffic
- **HTTP** (`jumpstarter-driver-http`) - HTTP client and server capabilities
- **SNMP** (`jumpstarter-driver-snmp`) - Simple Network Management Protocol
- **TFTP** (`jumpstarter-driver-tftp`) - Trivial File Transfer Protocol service

### Storage and Data Drivers

Drivers that control storage devices and manage data:

- **SDWire** (`jumpstarter-driver-sdwire`) - Access and control SD card
  interfaces
- **OpenDAL** (`jumpstarter-driver-opendal`) - Multi-cloud data access layer

### Media Drivers

Drivers that handle media streams:

- **UStreamer** (`jumpstarter-driver-ustreamer`) - Stream video from cameras

### Debug and Programming Drivers

Drivers for debugging and programming devices:

- **Probe-RS** (`jumpstarter-driver-probe-rs`) - Debug and program embedded
  devices
- **U-Boot** (`jumpstarter-driver-uboot`) - Interact with U-Boot bootloader
- **Flashers** (`jumpstarter-driver-flashers`) - Flash firmware to devices

### Utility Drivers

General-purpose utility drivers:

- **Shell** (`jumpstarter-driver-shell`) - Execute shell commands

### Specialized Hardware Drivers

Drivers for specific hardware platforms:

- **DUTLink** (`jumpstarter-driver-dutlink`) - Interface with DUTLink test
  hardware

### Composite Drivers

Composite drivers (`jumpstarter-driver-composite`) combine multiple lower-level
drivers to create higher-level abstractions or specialized workflows. For
example, a composite driver might coordinate power cycling, storage re-flashing,
and serial communication to automate a device initialization process.

In Jumpstarter, drivers are organized in a tree structure which allows for the
representation of complex device configurations that may be found in your
environment.

Here's an example of a composite driver tree:

```
MyHarness # Custom composite driver for the entire target device harness
├─ TcpNetwork # TCP Network driver to tunnel port 8000
├─ MyPower # Custom power driver to control device power
├─ SDWire # SD Wire storage emulator to enable re-flash on demand
├─ DigitalOutput # GPIO pin control to send signals to the device
└─ MyDebugger # Custom debugger interface composite driver
   └─ PySerial # Serial debugger with PySerial
```

## Driver Configuration

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

## Driver Communication

Drivers use two primary methods to communicate between client and exporter:

### Messages

Commands are sent as messages from driver clients to driver implementations,
allowing the client to trigger actions or retrieve information from the device.
Methods marked with the `@export` decorator are made available over the network.

### Streams

Drivers can establish streams for continuous data exchange, such as for serial
communication or video streaming. This enables real-time interaction with both
physical and virtual interfaces across the network.

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
