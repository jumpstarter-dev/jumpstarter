# Setup Local Mode

This guide shows you how to use Jumpstarter with a client and exporter running
on the same host.

## Prerequisites

Install [the following packages](../installation/packages.md) in your Python
environment:

- `jumpstarter-cli` - The Jumpstarter CLI for interacting with exporters
- `jumpstarter-driver-opendal` - The OpenDAL storage driver for file operations
- `jumpstarter-driver-power` - The base power driver for managing power states

These driver packages include mock implementations, enabling you to test the
connection between an exporter and client without physical hardware.

## Instructions

### Create an Exporter Configuration

Create an exporter configuration named `example-local` to define the
capabilities of your local test exporter. This configuration mirrors a regular
exporter config but without the `endpoint` and `token` fields since you
don't need to connect to the controller service.

Create `example-local.yaml` in `/etc/jumpstarter/exporters` with this content:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: example-local
export:
  storage:
    type: jumpstarter_driver_opendal.driver.MockStorageMux
  power:
    type: jumpstarter_driver_power.driver.MockPower
```

### Spawn an Exporter Shell

Interact with your local exporter using the "exporter shell" functionality in
the `jmp` CLI. When you spawn a shell, Jumpstarter runs a local exporter
instance in the background for the duration of your shell session.

```console
$ jmp shell --exporter example-local
```

### Exiting the Exporter Shell

To terminate the local exporter, simply exit the shell:

```console
$ exit
```

## Next Steps

Once you have your exporter shell running, you can start using Jumpstarter
commands to interact with your hardware. To learn more about common workflow
patterns and implementation examples, see [Examples](./examples.md).
