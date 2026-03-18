# Examples

This guide provides practical examples for using Jumpstarter in both local and
distributed modes. Each example demonstrates how to accomplish common tasks.

## Starting and Exiting a Session

Start a local exporter session:
```console
$ jmp shell --exporter example-local
```

Start a distributed exporter session:
```console
$ jmp shell --client hello --selector example.com/board=foo
```

When finished, simply exit the shell:
```console
$ exit
```

## Interact with the Exporter Shell

The exporter shell provides access to driver CLI interfaces through the magic
`j` command:

```console
$ jmp shell # Use appropriate --exporter or --client parameters
$ j
Usage: j [OPTIONS] COMMAND [ARGS]...

  Generic composite device

Options:
  --help  Show this message and exit.

Commands:
  power    Generic power
  storage  Generic storage mux
$ j power on
ok
$ j power off
ok
$ exit
```

When you run the `j` command in the exporter shell, you're accessing the CLI
interfaces exposed by the drivers configured in your exporter. In this example:

- `j power` - Would access the power interface from the MockPower driver
- `j storage` - Would access the storage interface from the MockStorageMux
  driver

Each driver can expose different commands through this interface, making it easy
to interact with the mock hardware. The command structure follows `j
<driver_type> <action>`, where available actions depend on the specific driver.

## Use the Python API in a Shell

The exporter shell exposes the local exporter via environment variables,
enabling you to run any Python code that interacts with the client/exporter.
This approach works especially well for complex operations or when a driver
doesn't provide a CLI.

### Using Python with Jumpstarter

Create a Python file for interacting with your exporter. This example
(`example.py`) demonstrates a complete power cycle workflow:

```python
import time
from jumpstarter.common.utils import env

with env() as client:
   client.power.on()
   client.power.off()
```

```console
$ jmp shell # Use appropriate --exporter or --client parameters
$ python ./example.py
$ exit
```

This example demonstrates how Python interacts with the exporter:

1. The `env()` function from `jumpstarter.common.utils` automatically connects
   to the exporter configured in your shell environment.

2. The `with env() as client:` statement creates a client connected to your
   local exporter and handles connection setup and cleanup.

3. `client.power.on()` directly calls the power driver's "on" method—the same
   action that `j power on` performs in the CLI.

4. `client.power.off()` directly calls the power driver's "off" method—the same
   action that `j power off` performs in the CLI.

Using a Python with Jumpstarter allows you to:

   - Create sequences of operations (power on → wait → power off)
   - Save and reuse complex workflows
   - Add logic, error handling, and conditional operations
   - Import other Python libraries (like `time` in this example)
   - Build sophisticated automation scripts

### Running `pytest` in the Shell

For structured test suites, Jumpstarter provides a `JumpstarterTest` base class
that handles connection management automatically. See the
[Testing with pytest](pytest-usage.md) guide for full details on writing tests,
custom fixtures, markers, and CI integration.
