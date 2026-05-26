# Power Driver

`jumpstarter-driver-power` provides functionality for interacting with power
control devices.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-power
```

## Configuration

Example configuration:

```{literalinclude} ../../../../../packages/jumpstarter-driver-power/examples/config.yaml
:language: yaml
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_power.client.PowerClient()
    :members: on, off, read, cycle
```
