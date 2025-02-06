# Drivers

Jumpstarter uses a modular driver model to build abstractions around the hardware
interfaces used to interact with a target device.

An [Exporter](./exporters.md) uses Drivers to "export" the hardware interfaces
from a host machine to the clients via [gRPC](https://grpc.io/).
Drivers can be thought of as a simplified API for an interface or device type.

Each driver consists of two components:
- An exporter-side module that implements the "backend" functionality of the driver.
- A client that provides a Python interface and optionally a CLI "frontend" for the driver.

While Jumpstarter comes with drivers for many basic interfaces, custom drivers
can also be developed for specialized hardware/interfaces or to provide
domain-specific abstractions for your use case.

## Driver Configuration

Drivers are configured using a YAML Exporter config file, which specifies
the drivers to load and the parameters for each. Drivers are distributed as Python
packages making it easy to develop and install your own drivers.

Here is an example exporter config that loads a custom driver called `jumpstarter_driver_dutlink`:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: demo
endpoint: grpc.jumpstarter.example.com:443
token: xxxxx
export:
  # The name to register the driver instance as
  dutlink:
    # A fully-qualified Python module
    type: jumpstarter_driver_dutlink.driver.Dutlink
    # Configuration parameters passed to the driver implementation
    config:
      storage_device: "/dev/disk/by-id/usb-SanDisk_3.2_Gen_1_5B4C0AB025C0-0:0"
  # Another driver instance for this device
  power:
    type: jumpstarter_driver_dutlink.driver.DutlinkPower
    config:
      serial: "c415a913" # serial number of the DUTLink board
```

## Driver I/O

All drivers are built upon the base `Driver` class, which provides abstractions
for utilizing Jumpstarter's gRPC transport to send messages and create streams
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

In Jumpstarter, drivers are organized in a tree structure which allows for the
representation of complex device trees that may be found in your environment.

For example, your team may use a custom test harness that connects to the host
via USB, but provides multiple hardware interfaces through a built-in USB hub.
Jumpstarter allows you to create a custom Composite Driver that provides a unified
interface to access all the interfaces provided by your harness.

Here is an example of a driver tree with two custom composite drivers:

```
MyHarness # Custom composite driver for the entire target device harness
├─ TcpNetwork # TCP Network driver to tunnel port 8000
├─ MyPower # Custom power driver to control device power
├─ SDWire # SD Wire storage emulator to enable re-flash on demand
├─ DigitalOutput # GPIO pin control to send signals to the device
└─ MyDebugger # Custom debugger interface composite driver
   └─ PySerial # Serial debugger with PySerial
```

The `jumpstarter_driver_composite` package provides the base `Composite` and
`CompositeClient` classes that can be used to build custom composite drivers.

A composite driver can also be used to orchestrate multiple interfaces to perform
a specific task such as flashing a system image or entering a debug mode. While
the driver may internally perform a complex task, a simple, user-friendly interface
can be provided to the driver clients.
