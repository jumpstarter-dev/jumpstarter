# Scripting

## Use the Python API in a Shell

The {term}`exporter shell` exposes the local {term}`exporter` via environment variables,
enabling you to run any Python code that interacts with the client/{term}`exporter`.
This approach works especially well for complex operations or when a driver
doesn't provide a CLI.

### Using Python with Jumpstarter

Create a Python file for interacting with your {term}`exporter`. This example
(`example.py`) demonstrates a complete power cycle workflow:

```{literalinclude} ../../../examples/getting-started/scripting_env.py
:language: python
```

```console
$ jmp shell # Use appropriate --exporter or --client parameters
$ python ./example.py
$ exit
```

This example demonstrates how Python interacts with the {term}`exporter`:

1. The `env()` function from `jumpstarter.common.utils` automatically connects
   to the {term}`exporter` configured in your shell environment.

2. The `with env() as client:` statement creates a client connected to your
   local {term}`exporter` and handles connection setup and cleanup.

3. `client.power.on()` directly calls the power driver's "on" method--the same
   action that `j power on` performs in the CLI.

4. `client.power.off()` directly calls the power driver's "off" method--the same
   action that `j power off` performs in the CLI.

Using a Python with Jumpstarter allows you to:

   - Create sequences of operations (power on -> wait -> power off)
   - Save and reuse complex workflows
   - Add logic, error handling, and conditional operations
   - Import other Python libraries (like `time` in this example)
   - Build sophisticated automation scripts

### Running `pytest` in the Shell

For structured test suites, Jumpstarter provides a `JumpstarterTest` base class
that handles connection management automatically. See the
[Testing](testing.md) guide for full details on writing tests,
custom fixtures, markers, and CI integration.
