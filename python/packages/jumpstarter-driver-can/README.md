# CAN driver

`jumpstarter-driver-can` provides functionality for interacting with CAN bus connections.

## Installation

```bash
pip install jumpstarter-driver-can
```

## Configuration

Example configuration:

```yaml
interfaces:
  can:
    driver: jumpstarter_driver_can.CANDriver
    parameters:
      # Add required parameters here
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_can.client.CanClient()
    :members:
```

```{eval-rst}
.. autoclass:: jumpstarter_driver_can.client.IsoTpClient()
    :members:
```
