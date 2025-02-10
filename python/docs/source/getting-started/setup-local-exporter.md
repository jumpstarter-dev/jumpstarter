# Setup a Local Exporter

This guide walks you through the process of using Jumpstarter with a local
exporter (i.e. the client and the exporter running on the same host).

## Prerequisites

Make sure the following packages are installed in your Python environment:
- `jumpstarter-cli` - The core Jumpstarter CLI.
- `jumpstarter-driver-opendal` - The OpenDAL storage driver.
- `jumpstarter-driver-power` - The base power driver.

```{tip}
Both of these driver packages provide mock implementations, this makes it easier
to debug the connection between an exporter and client without hardware.
```

## Create an Exporter Config

First, we must create an exporter config to define the "shape" of the exporter
that we are going to test locally. This config is identical to a regular exporter
config, however, the `endpoint` and `token` fields may be left empty as we do
not need to connect to the controller service.

Create a text file in `/etc/jumpstarter/exporters` with the following content:

```{note}
The name of this file is used when referring to the exporter in later steps.
```

```yaml
# /etc/jumpstarter/exporters/demo.yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: demo
# endpoint and token are intentionally left empty
endpoint: ""
token: ""
# mock drivers for demo purpose
export:
  storage:
    type: jumpstarter_driver_opendal.driver.MockStorageMux
  power:
    type: jumpstarter_driver_power.driver.MockPower
```

## Spawn an Exporter Shell

To interact locally with the exporter we created above, we can use the
"exporter shell" functionality within the `jmp` CLI. When a shell is spawned,
a local exporter instance is run in the background while the shell session is
running.

```shell
# Spawn a new exporter shell for "demo"
$ jmp exporter shell demo
```

### Interact with the Exporter Shell

If the drivers specified in the exporter config provide a CLI interface, it will
be available though the magic `j` command within the exporter shell.

```shell
# Enter the shell
$ jmp exporter shell demo

# Running inside exporter shell
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

### Use the Python API in a Shell

As the shell exposes the local exporter via environment variables, we can run
any command or Python script that interacts with a client/exporter.

This allows us to use the Python API directly without having to interact with
the CLI. This is often useful for more complex scripts or if a specific driver
doesn't provide a client CLI.

#### Running a Python Script

The easiest way to interact with the exporter is to run a quick Python script
directly from the command line. This comes in handy when no CLI is available.

```shell
# Enter the shell
$ jmp exporter shell demo
# Running python inside exporter shell
$ python - <<EOF
from jumpstarter.common.utils import env
with env() as client:
    print(client.power.on())
EOF
ok
# Exit the shell
$ exit
```

#### Running a Python File

For more complex use cases, a Python file can also be run directly from within
the shell.

```python
# demo.py
import time

from jumpstarter.common.utils import env

with env() as client:
    # Power on the device
    print("Power on")
    client.power.on()

    # Wait three seconds
    print("Waiting 3 seconds...")
    time.sleep(3.0)

    # Power off the device
    print("Power off")
    client.power.off()

    print("Done!")
```

```shell
# Enter the shell
$ jmp exporter shell demo
# Running python inside exporter shell
$ python ./demo.py
# Exit the shell
$ exit
```

#### Running `pytest` in the Shell

If you are running multiple test cases, it may make sense to run a `pytest`
suite instead. Jumpstarter provides a built-in testing library called
`jumpstarter_testing` which provides the `JumpstarterTest` fixture.

```python
# mytest.py
from jumpstarter_testing.pytest import JumpstarterTest

class MyTest(JumpstarterTest):
    def test_power_on(self, client):
        client.power.on()

    def test_power_off(self, client):
        client.power.off()
```

```shell
# Enter the shell
$ jmp exporter shell demo
# Running python inside exporter shell
$ pytest ./mytest.py
# Exit the shell
$ exit
```

### Exiting the Exporter Shell

Once you are done with the exporter, simply exit the exporter shell and the
local exporter will be terminated.
