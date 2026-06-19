# OBD-II Driver

`jumpstarter-driver-obd` wraps [python-obd](https://python-obd.readthedocs.io/en/latest/)
to query On-Board Diagnostics (OBD-II) PIDs from a vehicle ECU via an ELM327 adapter.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-obd
```

## Hardware Requirements

- An ELM327 USB adapter (e.g. PremiumCord ELM327 USB)
- A vehicle with an OBD-II port (petrol cars from 2001+, diesel from 2004+, all US cars from 1996+)
- **macOS only**: ELM327 USB cables use a CH340 or CP2102 USB-serial chip that may need a kernel
  extension -- install the appropriate driver if the port does not appear after plugging in

## Configuration

### Auto-detect (recommended for a single adapter)

Leave `port` unset or `null` to let python-obd scan available serial ports and connect to the first
ELM327 it finds:

```yaml
export:
  obd:
    type: jumpstarter_driver_obd.driver.OBD
    config:
      port: null
```

```{note}
Auto-detect is reliable on Linux (e.g. `/dev/ttyUSB0`). On **macOS** it is not: python-obd
enumerates the `/dev/tty.*` call-in device nodes, but ELM327 adapters only open on the matching
`/dev/cu.*` call-out node, so auto-detect fails to connect. On macOS, pass an explicit
`/dev/cu.usbserial-*` port instead.
```

### Explicit port

Specify the serial port directly when you have multiple adapters or want a deterministic setup:

```yaml
export:
  obd:
    type: jumpstarter_driver_obd.driver.OBD
    config:
      port: /dev/ttyUSB0       # Linux
      # port: /dev/cu.usbserial-XXXX   # macOS
      baudrate: 38400
```

### Config parameters

| Parameter | Description | Type | Required | Default |
|-----------|-------------|------|----------|---------|
| port | Serial port path; `null` to auto-detect an ELM327 adapter | str \| null | no | null |
| baudrate | ELM327 baud rate | int | no | 38400 |
| fast | Enable ELM327 fast mode (~100-400 ms faster per query); unreliable on cheap clone adapters, so it is opt-in | bool | no | false |

## Usage

```python
from jumpstarter_driver_obd import OBDConnectionStatus

# Check connection state
status = obd.status()                              # returns OBDConnectionStatus
print(status == OBDConnectionStatus.CAR_CONNECTED) # True
print(obd.is_connected())                          # True

# Discover what the ECU supports
cmds = obd.supported_commands()   # ["RPM", "SPEED", "COOLANT_TEMP", ...]

# Query individual PIDs by name
rpm   = obd.query("RPM")          # "3000.0 revolutions_per_minute"
speed = obd.query("SPEED")        # "60.0 kph"
temp  = obd.query("COOLANT_TEMP") # "90.0 degC"

# Clearing trouble codes is a separate, explicit call (see note below)
obd.clear_dtc()
```

`query()` returns `None` when the ECU does not answer the command (unsupported PID
or no vehicle on the bus). It is read-only: it refuses destructive commands such as
`CLEAR_DTC` (OBD-II mode 04, which erases stored trouble codes and resets emissions
readiness monitors). To clear codes deliberately, call `clear_dtc()` instead.

## Troubleshooting

**Port not found on macOS**
Run `ls /dev/cu.*` before and after plugging in the cable to spot the new device.
Install a CH340 or CP2102 USB-serial kernel extension if no new port appears.

**Permission denied on Linux**
Add your user to the `dialout` group and log out/in:
```shell
sudo usermod -aG dialout $USER
```
Alternatively, create a udev rule for the adapter's USB vendor/product ID.

**"No OBD-II adapters found"**
Make sure the cable is seated in the OBD-II port (typically under the dashboard on the driver's side)
and that the vehicle ignition is in the ON or ACC position.

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_obd.driver.OBD()
    :members:

.. autoclass:: jumpstarter_driver_obd.client.OBDClient()
    :members:

.. autoclass:: jumpstarter_driver_obd.driver.OBDConnectionStatus()
    :members:
```
