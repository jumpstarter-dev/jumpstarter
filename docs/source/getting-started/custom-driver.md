# Custom Drivers

Jumpstarter provides an easy-to-use interface so you can quickly develop custom
drivers for your own hardware or interfaces not natively supported by Jumpstarter.

Each driver consists of two components:
- A driver class that implements the "backend" functionality of the driver.
- A client class that provides a Python and CLI "frontend" for the driver.

## Writing a Driver

A custom driver allowing you to run arbitrary commands on the exporter would
look like this:

```python
# jumpstarter_custom_driver/__init__.py
from jumpstarter.driver import Driver, export
from jumpstarter.client import DriverClient

# Exporter-side "backend" of driver
class CustomDriver(Driver):
    """
    A custom driver class.
    """

    # Jumpstarter uses this method to get the import path of the client class
    @classmethod
    def client(cls) -> str:
        # This should be your driver client import path
        return "jumpstarter_custom_driver.CustomClient"

    # A generic method that can be invoked over gRPC from the client
    @export # Export the method to make it available to clients
    def execute(self, command: str, args: list[str]) -> str: # Only positional arguments are allowed
        """
        Execute a command locally (UNSAFE).
        """
        result = run_command(command, args) # Run the command in shell, etc.
        return result # Return a simple type result via gRPC

# Client-side Python interface
class CustomClient(DriverClient):
    """
    A custom driver client class.
    """

    # We suggest that you write wrapper methods to invoke the driver from the client
    def execute(self, command: str, args: list[str]) -> str:
        """
        Execute a command on the exporter host (UNSAFE).
        """
        # `self.call` is provided by the `DriverClient` base class
        # which can be used to transparently call exporter-side methods by name.
        # The parameters and return values are serialized with Protobuf,
        # so only simple types like str, int, float, list, and dict are supported.
        return self.call("execute", command, args)

    # Additional client-only helper methods can also be provided for convenience
    def ls(self) -> str:
        """
        Execute the `ls` command on the exporter host.
        """
        # Using execute method to call the exporter side
        return self.execute("ls", [])

    def rm(self, files: list[str]) -> str:
        """
        Execute the `rm <files>` command on the exporter host.
        """
        return self.execute("rm", files)
```

## Installing a Driver

All Jumpstarter Drivers should be distributed as a Python package and installed
on both the exporter host machine and any client machines.

```{tip}
It may be useful to use an internal package repository such as GitHub Packages
to distribute custom driver packages to all your users.
```

Here is an example `pyproject.toml` file for a custom driver.

```toml
# pyproject.toml
[project]
name = "jumpstarter-custom-driver"
version = "0.0.1"
dependencies = [
  "jumpstarter",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

```{note}
It is important that your driver client/exporter package versions are compatible
or backwards-compatible. We recommend treating drivers APIs like Protobufs and
versioning in a backwards-compatible way to prevent issues.
```

## Using a Driver

Custom drivers can be used in the same way as Jumpstarter's built-in driver packages.

Here is an exporter config file that uses the custom driver from above:

```yaml
# /etc/jumpstarter/exporters/custom.yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
endpoint: ""
token: ""
export:
    custom:
        # Full import path of the driver class
        type: jumpstarter_driver_custom.CustomDriver
```

## Advanced Drivers

Drivers can also take configuration parameters, export generator methods,
async methods, or TCP-like byte streams. For writing advanced drivers using
these features, please refer to the [Driver API Reference](#driver-api).
