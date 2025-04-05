# Manage Exporters

The `jmp admin` admin CLI can be used to manage your exporter configurations
on the distributed service.

## Creating a exporter

If you have configured [a Jumpstarter service](../introduction/service.md)
and you have a kubeconfig, the [`jmp admin` CLI](./reference/jmp-admin.md#jmp-admin-create-exporter)
will attempt to use your current credentials to provision the client automatically, and produce
a base exporter configuration file.

To connect a target device to Jumpstarter, an exporter instance must be registered.

Exporter creation must be done by an administrator user who has access to
the Kubernetes cluster where the `jumpstarter-controller` service is hosted.

```bash
# Create the exporter instance
$ jmp admin create exporter my-exporter --namespace jumpstarter-lab -o my-exporter.yaml
```

This creates an exporter named `my-exporter` and produces a YAML configuration file `my-exporter.yaml`:
```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: jumpstarter-lab
  name: my-exporter
endpoint: grpc.jumpstarter.example.com:443
token: <<token>>

```

`my-exporter.yaml` should be configured with the desired exported drivers filling up the
export section, see [exporter configuration docs](../config.md#exporter-config) for more details.

### Example configuration
If you don't have the hardware ready yet but you want to try things out you
can setup the exporter with something like the following example which
will provide a few mock interfaces to play with:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: demo
endpoint: grpc.jumpstarter.192.168.1.10.nip.io:8082
token: <<token>>
tls:
    ca: ''
    insecure: True
export:
    storage:
        type: jumpstarter_driver_opendal.driver.MockStorageMux
    power:
        type: jumpstarter_driver_power.driver.MockPower
    echonet:
        type: jumpstarter_driver_network.driver.EchoNetwork
    can:
        type: jumpstarter_driver_can.driver.Can
        config:
            channel: 1
            interface: "virtual"
```

Once the exporter configuration is ready it should be installed in the
exporter host machine at
`/etc/jumpstarter/exporters/my-exporter.yaml`.

```{note}
Remember, the exporter is a Linux service that exports the interfaces to the target DUT(s)
(serial ports, video interfaces, bluetooth, anything that Jumpstarter has a driver for,
and the exporter service can reach via linux device or network). In this case the exporter
service calls back to the Jumpstarter service to report the available interfaces and
waits for commands.
```

```{tip}
For information on how to run and setup a exporter, see the [exporter config section](../config.md#running-an-exporter).
```
