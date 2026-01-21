# Exporters

Jumpstarter uses a program called an Exporter to enable remote access to your
hardware. The Exporter typically runs on a "host" system directly connected to
your hardware. It is called an Exporter because it "exports" the interfaces
connected to the target device for client access.

## Hosts

Typically, the host will be a low-cost test system such as a single board
computer with sufficient interfaces to connect to your hardware. It is also
possible to use a local high-power server (or CI runner) as the host device.

A host can run multiple Exporter instances simultaneously if it needs to
interact with several different devices at the same time.

## Exporter Configuration

Exporters use a YAML configuration file to define which Drivers must be loaded
and the configuration required.

Here is an example Exporter config file which would typically be saved at
`/etc/jumpstarter/exporters/demo.yaml`:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: demo
endpoint: grpc.jumpstarter.example.com:443
token: xxxxx
grpcConfig:
    grpc.keepalive_time_ms: 20000
export:
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
  storage:
    type: "jumpstarter_driver_sdwire.driver.SDWire"
    config:
      serial: "sdw-00001"
      storage_device: "/dev/disk/by-path/..."
  custom:
    type: "vendorpackage.CustomDriver"
    config:
      hello: "world"
  reference:
    ref: "power"
```

Note that the `grpcConfig` section supports all options documented in the [gRPC
argument keys
documentation](https://grpc.github.io/grpc/core/group__grpc__arg__keys.html).

## Running an Exporter

To run an Exporter on a host system, you must have Python {{requires_python}}
installed and the driver packages specified in the config installed in your
current Python environment.

You can run the exporter in your local terminal with:

```console
$ jmp run --exporter myexporter
```

Exporters can also be run in a privileged container or as a systemd daemon. It
is recommended to run the Exporter service in the background with auto-restart
capabilities in case something goes wrong and it needs to be restarted.
