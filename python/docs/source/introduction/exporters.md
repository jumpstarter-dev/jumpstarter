# Exporters

Jumpstarter uses a program called an Exporter to enable remote access to your
hardware. The Exporter typically runs on a "host" system directly connected
to your hardware. We call it an Exporter because it "exports" the interfaces
connected to the target for client access.

## Hosts

Typically, the host will be a low-cost test system such as a Raspberry Pi
or Mini PC with sufficient interfaces to connect to your hardware. It is also
possible to use a local high-power server (or CI runner) as the host device.

A host can run multiple Exporter instances simultaneously if it needs to interact
with several different devices at the same time.

## Configuration

Exporters use a YAML configuration file to define which Drivers must be loaded
and the configuration required.

Here is an example Exporter config file:

```yaml
# /etc/jumpstarter/exporters/myexporter.yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: demo
endpoint: grpc.jumpstarter.example.com:443
token: xxxxx
export:
  # a DUTLink interface to the DUT
  dutlink:
    type: jumpstarter_driver_dutlink.driver.Dutlink
    config:
      storage_device: "/dev/disk/by-id/usb-SanDisk_3.2_Gen_1_5B4C0AB025C0-0:0"
```

## Running an Exporter

To run an Exporter on a host system, you must have Python {{requires_python}} installed
and the driver packages specified in the config installed in your current Python
environment.

```bash
# Run the exporter myexporter in your local terminal
$ jmp exporter run myexporter
```

Exporters can also be run in a privileged container or as a systemd daemon. It is
recommended to run the Exporter service in the background with auto-restart
in case something goes wrong and it needs to be restarted.
