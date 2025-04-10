# CAN driver

`jumpstarter-driver-can` provides functionality for interacting with CAN bus connections.

## Installation

```shell
pip install jumpstarter-driver-can
```

## Configuration

Example configuration:

```yaml
export:
  can:
    type: jumpstarter_driver_can.Can
    config:
      # Add required config parameters here
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
