# Drivers

Jumpstarter uses a modular driver model to build abstractions around hardware
interfaces used to interact with a target device.

The Exporter uses Drivers to "export" the hardware interfaces from the host machine
to the clients via gRPC. Drivers can be thought of as a simplified API to interface
with specific hardware.

Each driver consists of two components:
- A driver class that implements the "backend" functionality of the driver.
- A client class (optional) that provides a Python and CLI "frontend" for the driver.

While Jumpstarter comes with drivers for many basic interfaces, custom drivers
can also be developed for specialized hardware/interfaces or to provide
domain-specific abstractions for your use case.

## Driver Configuration

Drivers are configured using the Exporter config file, which specifies the drivers
to load and the parameters for each driver.

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

All drivers are based off of the base `Driver` class which provides abstractions
to use Jumpstarter's gRPC transport for sending messages and creating streams
to tunnel data between the exporter and the client.

## Composite drivers

Multiple drivers can be combined to create a Composite Driver with additional
device-specific functionality for your use case. For example, you may want to
develop a composite driver that provides methods that simulate the physical wiring
harness your device will use in production.
