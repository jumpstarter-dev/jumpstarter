# Qnx Driver

`jumpstarter-driver-qnx` provides functionality for interacting with qnx devices.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-qnx
```

## Configuration

Example configuration:

```yaml
export:
  qnx:
    type: jumpstarter_driver_qnx.driver.Qnx
    config:
      # Add required config parameters here
```

<!-- Optional: uncomment if this driver has usage examples or CLI commands
## Usage

Add usage examples, CLI commands, and common workflows here.
-->

<!-- Optional: uncomment for complex drivers with non-trivial internals
## Architecture

Describe the internal design and component interactions here.
-->

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_qnx.driver.Qnx()
```

<!-- Optional: uncomment for drivers with known issues or complex setup
## Troubleshooting

Document common issues and their solutions here.
-->
