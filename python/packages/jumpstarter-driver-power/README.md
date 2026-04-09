# Power driver

`jumpstarter-driver-power` provides functionality for interacting with power
control devices.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-power
```

## Configuration

Example configuration:

```yaml
export:
  power:
    type: jumpstarter_driver_power.driver.MockPower
    config:
      # Add required config parameters here
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_power.client.PowerClient()
    :members: on, off, read, cycle
```