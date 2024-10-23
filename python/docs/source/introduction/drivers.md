# Drivers

Jumpstarter uses a modular driver model to build abstractions around the hardware
interfaces used to interact with a target device.

An [Exporter](./exporters.md) uses Drivers to "export" the hardware interfaces
from the host machine to the clients via gRPC. Drivers can be thought of as a
simplified API for an interface or device type.

Each driver consists of two components:
- A driver class that implements the "backend" functionality of the driver.
- A client class (optional) that provides a Python and CLI "frontend" for the driver.

While Jumpstarter comes with drivers for many basic interfaces, custom drivers
can also be developed for specialized hardware/interfaces or to provide
domain-specific abstractions for your use case.

## Driver Configuration

Drivers are configured using the a YAML Exporter config file, which specifies
the drivers to load and the parameters for each. Drivers are distributed as Python
packages making it easy to develop and install your own drivers.

Here is an example exporter config that loads a driver:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
endpoint: grpc.jumpstarter.example.com:443
token: xxxxx
export:
  # a DUTLink interface to the DUT
  dutlink:
    type: jumpstarter_driver_dutlink.driver.Dutlink
    config:
      storage_device: "/dev/disk/by-id/usb-SanDisk_3.2_Gen_1_5B4C0AB025C0-0:0"
```

## Driver I/O

All drivers are based off of the base `Driver` class which provides abstractions
to use Jumpstarter's gRPC transport for sending messages and creating streams
to tunnel data between the exporter and the client.

### Messages

Individual commands can be sent as messages from the driver client to a driver
instance running in the Exporter. These commands are automatically sent over
Jumpstarter's gRPC connection between the Client and Exporter.

### Streams

Drivers can also create and manage gRPC streams to pass large files, stream network
traffic, and emulate I/O devices across the network. Some examples of streams are
TCP port forwarding and CAN bus emulation.

## Composite Drivers

In Jumpstarter, drivers are are organized in a tree structure which allows for
the development of hierarchical relationships between interfaces and their
associated devices.

Multiple drivers can be combined to create a Composite Driver with additional
device-specific functionality for your use case.

Here is an example of a driver tree with two custom composite drivers:

```yaml
MyDevice # Custom composite driver for the entire target device
├─ TcpNetwork # TCP Network driver to tunnel port 8000
├─ MyPower # Custom power driver to control device power
├─ SDWire # SD Wire storage emulator to enable re-flash on demand
├─ DigitalOutput # GPIO pin control to send signals to the device
└─ MyDebugger # Custom debugger interface composite driver
   └─ PySerial # Serial debugger with PySerial
```

Creating a composite driver can automate common tasks related to your specific
hardware configuration such as flashing a system image or entering a debug mode.

For example, you may want to develop a composite driver that enables a test script
to perform a complex action that requires multiple other driver interfaces with
a single method invocation.

