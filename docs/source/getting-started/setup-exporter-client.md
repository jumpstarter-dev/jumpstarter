# Setup a Remote Exporter/Client

This guide walks you through the process of creating an exporter using the
controller service, configuring drivers, and running the exporter.

## Prerequisites

Make sure [the following packages are
installed](../installation/python-package.md) in your Python environment:
- `jumpstarter-cli` - The core Jumpstarter CLI.
- `jumpstarter-driver-opendal` - The OpenDAL storage driver.
- `jumpstarter-driver-power` - The base power driver.

You should also have the [Jumpstarter Service](../introduction/service.md)
running in a Kubernetes cluster you have admin access to. For instructions on
how to install Jumpstarter in a cluster, refer to the [installation
guide](../installation/service/index.md).

```{tip}
Make sure you have the correct cluster in your `kubeconfig` file and the right
context selected.
```

## Create an Exporter

First, we must create an exporter using the controller service API. The `jmp
admin` CLI provides methods to interact with the controller directly.

To create an exporter and save the config locally, run the following command:

```bash
# Creates an exporter called "testing" and saves the config
$ jmp admin create exporter testing --save
```

## Usage for jmp admin create exporter

```{command-output} jmp admin create exporter --help
```

### Edit the Exporter Config

Once the exporter has been created, a new config file will be saved to
`/etc/jumpstarter/exporters/testing.yaml`.

To edit the config file with your default text editor, run the following
command:

```bash
# Opens the config for "testing" in your default editor
$ jmp config exporter edit testing
```

Add the `storage` and `power` drivers under the `export` field in the config
file. The finished config should look like this:

```yaml
# /etc/jumpstarter/exporters/testing.yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
# These values are automatically filled by the controller
endpoint: "..."
token: "..."
# Mock drivers for demo purpose
export:
  storage:
    type: jumpstarter_driver_opendal.driver.MockStorageMux
  power:
    type: jumpstarter_driver_power.driver.MockPower
```

## Run an Exporter

To run the exporter locally, we can use the `jmp` CLI tool.

Run the following command to start the exporter locally using the config file:

```bash
# Runs the exporter "testing" locally
$ jmp run --exporter testing
```

The exporter will stay running until the process is exited via `^C` or the shell
is closed.

## Create a Client

To connect to the new exporter, a client must be created. We can also use the
`jmp admin` CLI tool to create a client using the controller.

```bash
# This will create a client called "hello", allow unsafe drivers, and save the config
$ jmp admin create client hello --save --unsafe
```

## Usage for jmp admin create client

```{command-output} jmp admin create client --help
```

## Connect to the Exporter

To interact with the exporter we created above, we can use the "client shell"
functionality within the `jmp` CLI. When a shell is spawned, the client attempts
to acquire a lease on an exporter. Once the lease is acquired, the client can be
interacted with through the magic `j` command or via the Python API.

```bash
# Spawn a shell using the "hello" client
$ jmp shell --client hello --selector example.com/board=foo
```

## Usage for jmp shell

```{command-output} jmp shell --help
```

Once a lease is acquired, we can interact with the drivers hosted by the
exporter within the shell instance.

```bash
# Spawn a shell using the "hello" client
$ jmp shell --client hello --selector example.com/board=foo

# Running inside client shell
$ j
Usage: j [OPTIONS] COMMAND [ARGS]...

  Generic composite device

Options:
  --help  Show this message and exit.

Commands:
  power    Generic power
  storage  Generic storage mux

# Simulate turning on the power
$ j power on
ok

# Exit the shell
$ exit
```
