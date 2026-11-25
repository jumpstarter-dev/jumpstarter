# Composite Driver

`jumpstarter-driver-composite` provides functionality for interacting with composite devices.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-composite
```

The `jumpstarter` package depends on it, so it is normally installed already.

## Configuration

Example configuration:

```yaml
export:
  composite:
    type: jumpstarter_driver_composite.driver.Composite
    children:
      power:
        type: jumpstarter_driver_power.driver.MockPower
      serial:
        type: jumpstarter_driver_pyserial.driver.PySerial
        config:
          url: "/dev/ttyUSB0"
```

`Composite` has no config parameters of its own. It groups the drivers listed
under `children`, which a client reaches as attributes
(`client.composite.power`) and on the command line as `j composite <child> ...`.
The command group of every composite, the top-level `j` included, accepts
`--log-level` (`DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`), which sets
the level of every logger in the client process.

An entry given as `ref:` instead of `type:` is a proxy
(`jumpstarter_driver_composite.driver.Proxy`) for another driver of the same
exporter, named by its dotted path from the top of `export`, for example
`ref: composite.power`.

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_composite.driver.Composite()
```
