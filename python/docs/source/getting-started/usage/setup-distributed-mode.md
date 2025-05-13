# Setup Distributed Mode

This guide walks you through the process of creating an exporter using the
controller service, configuring drivers, and running the exporter.

```{warning}
The jumpstarter-controller endpoints are secured by TLS. However, in release 0.6.x,
the certificates are self-signed and rotated on every restart. This means the client
will not be able to verify the server certificate. To bypass this, you should use the
`--insecure-tls-config` flag when creating clients and exporters. This issue will be
resolved in the next release. See [issue #455](https://github.com/jumpstarter-dev/jumpstarter/issues/455)
for more details.
Alternatively, you can configure the ingress/route in reencrypt mode with your own key and certificate.
```

## Prerequisites

Install [the following packages](../installation/packages.md) in your Python
environment:

- `jumpstarter-cli` - The core Jumpstarter CLI
- `jumpstarter-driver-opendal` - The OpenDAL storage driver
- `jumpstarter-driver-power` - The base power driver

These driver packages include mock implementations, enabling you to test the
connection between an exporter and client without physical hardware.

You need the [service](../../introduction/service.md) running in a Kubernetes
cluster with admin access. For installation instructions, refer to the
[installation guide](../installation/service.md).

## Instructions

### Create an Exporter Configuration

Create an exporter using the controller service API. The `jmp admin` CLI
provides commands to interact with the controller directly.

Run this command to create an exporter named `example-distributed` and save the
configuration locally:

```console
$ jmp admin create exporter example-distributed --label foo=bar --save --insecure-tls-config
```

After creating the exporter, find the new configuration file at
`/etc/jumpstarter/exporters/example-distributed.yaml`. Edit the configuration
using your default text editor with:

```console
$ jmp config exporter edit example-distributed
```

Add the `storage` and `power` drivers under the `export` field in the
configuration file. Your configuration should look like this:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: example-distributed
endpoint: "<automatically filled by the controller>"
token: "<automatically filled by the controller>"
export:
  storage:
    type: jumpstarter_driver_opendal.driver.MockStorageMux
  power:
    type: jumpstarter_driver_power.driver.MockPower
```

### Run an Exporter

Start the exporter locally using the `jmp` CLI tool:

```console
$ jmp run --exporter example-distributed
```

The exporter runs until you terminate the process with or close the shell.

### Create a Client

Create a client to connect to your new exporter using the `jmp admin` CLI:

The following command creates a client named "hello", enables unsafe drivers for
development purposes, and saves the configuration locally in
`${HOME}/.config/jumpstarter/clients/`:

```console
$ jmp admin create client hello --save --unsafe --insecure-tls-config
```

### Spawn an Exporter Shell

Interact with your distributed exporter using the "client shell" functionality
in the `jmp` CLI. When you spawn a shell, the client attempts to acquire a lease
on an exporter. Once the lease is acquired, you can interact with the exporter
through your shell session.

```console
$ jmp shell --client hello --selector example.com/board=foo
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
