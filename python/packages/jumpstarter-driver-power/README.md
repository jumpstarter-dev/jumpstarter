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

```yaml
export:
  power:
    type: jumpstarter_driver_power.driver.MockPower
    config:
      # Add required config parameters here
```

## Flashing interlock

Exporters that flash devices with the {doc}`fastboot driver <fastboot>` protect
power drivers (anything implementing `PowerInterface` or
`VirtualPowerInterface`) while a flash is writing. `off` and other switching
calls are refused, session-start `reset()` is skipped, and session-end
`close()` is deferred until the flash is safe. By default every power driver in
the exporter is protected; on an exporter that powers several devices, set each
flasher's `interlock_power` to its device's power driver path (e.g.
`["pdu.outlet3"]`) so the others stay usable. Declare anything that switches a
DUT's supply as a power driver (for a relay channel, gpiod `PowerSwitch` rather
than `DigitalOutput`) so it is recognized.

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_power.client.PowerClient()
    :members: on, off, read, cycle, status
```

`status` is optional for power drivers. It returns the state the driver reports
(for example `on`, `off`, or `unknown`), or `None` when the driver cannot report
its state. `j power status` exits with an error in that case.