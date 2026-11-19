# CoPilot driver

`jumpstarter-driver-copilot` controls the GPIO outputs of a
[BayLibre CoPilot](https://github.com/BayLibre/Copilot) through Linux libgpiod.
One controller owns the managed GPIO lines; multiple output, power and USB
adapters share that request through Jumpstarter references. The driver supports
explicit relay and USB HAT wiring and reconnects to the selected USB unit without
replaying interrupted writes.

Hardware references:

- CoPilot: [hardware repository](https://github.com/BayLibre/Copilot) and
  [documentation](https://baylibre.github.io/Copilot/)
- [Triple-relay HAT](https://github.com/BayLibre/Copilot_Extension_Triple_Relays)
- [Triple-USB 2.0 HAT](https://github.com/BayLibre/Copilot-extension-triple-usb-2.0)

## Installation and host setup

```shell
pip install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-copilot
```

The exporter requires Linux, a kernel exposing the CoPilot GPIO chip, and the
libgpiod Python bindings version 2.2 or newer. The client may run on other platforms.

Install the udev rules from the official
[CoPilot setup guide](https://baylibre.github.io/Copilot/getting-started.html#udev-rules).
They create `/dev/copilot/by-id/<serial>/gpiochip` and the UART symlink. Reload the
rules and reconnect the unit as described there. A system service also needs
access to the GPIO device: configure its service account/group explicitly, since
an interactive desktop's `uaccess` ACL may not grant access to the exporter.
The driver does not install rules, change permissions or bind kernel drivers.

## Supported configurations

| Configuration | Available functionality | Example file |
|---------------|-------------------------|--------------|
| CoPilot without a HAT | Native supply control and GPIO outputs | `examples/exporter-base.yaml` |
| CoPilot UART | Board serial console/logs through the existing PySerial driver; no HAT required | `examples/exporter-uart.yaml` |
| Triple-relay HAT | Three configurable relay channels, GPIO readback and pulses | `examples/exporter.yaml` |
| Triple-USB HAT | Per-port power control with `usb_lines`: on, off, read and exporter-side cycle | `examples/exporter-usb.yaml` |

The controller currently manages **outputs only**, not GPIO inputs. Readback is
of an output GPIO; it does not measure supply voltage or confirm physical relay
contact closure. The upstream candidate is covered by simulated GPIO and real
RPC tests; these examples are not a claim of physical validation on every HAT.

For a complete bench with native supply, three named relay contacts and UART,
use `examples/exporter-relay-uart.yaml`. The wiring table, local startup,
boot-log capture and contact workflows are described in `examples/README.md`.
The relay-only example requires no PySerial installation. All examples start
with inactive outputs and require adaptation to the actual DUT wiring.

## Controller configuration

| Setting | Default | Meaning |
|---------|---------|---------|
| `serial` | unset | Select a unit through its stable udev link. With neither selector set, exactly one unit must be present. |
| `device` | unset | Explicit GPIO device path; mutually exclusive with `serial`. Does not guarantee USB identity after replacement. |
| `managed_lines` | `[0, 1, 2, 3]` | Distinct non-negative GPIO offsets owned by this controller. |
| `active_low` | `false` | Logical polarity, shared by all managed lines. Native power requires `false`. |
| `initial_active` | `false` | Initial assertion of **all** managed outputs, on first acquisition only. Reconnect always starts inactive. |
| `hats` | `[]` | List of explicit accessory descriptions; no automatic detection. |

### Accepted HAT descriptions

The field is `hats`, a list of mappings, each with a `type`:

| `type` | Parameters | Effect |
|--------|------------|--------|
| `triple-relay` | `relay_lines`: three distinct integer GPIO offsets, all in `managed_lines` | Maps relay channels 1–3 to GPIOs and enables the relay commands. At most one such entry is supported. |
| `usb-extension` | Optional `ports`: a positive integer; optional `usb_lines`: distinct GPIO offsets in port order, all in `managed_lines` | With `usb_lines`, enables per-port control through `CoPilotUSB`. Without it, inventory metadata only. |
| `custom` | No driver-defined control parameters | Inventory metadata for another accessory; does not implement an accessory protocol. |

Omit `hats` or use `hats: []` for the base board. Other `type` values are rejected.
If `relay_lines` is omitted it falls back to `managed_lines`, which must then
contain exactly three lines. Specify it explicitly when also managing GPIO0.
`custom` is a generic software description, not a third BayLibre HAT model.

## CoPilot without a HAT

```yaml
export:
  copilot:
    type: jumpstarter_driver_copilot.driver.CoPilotGPIO
    config:
      serial: YOUR_COPILOT_SERIAL
      managed_lines: [0, 1, 2, 3]
      hats: []
  power:
    type: jumpstarter_driver_copilot.driver.CoPilotPower
    children:
      controller:
        ref: copilot
```

The `copilot` export provides GPIO access. The separate `power` export adds the
standard power client and a switch-state query; it requires managed GPIO0.
Inside a Jumpstarter shell:

```shell
j copilot info
j power on
j power state
j power off
```

Native power requires J1 to select GPIO-controlled supply passthrough.
`state()` reports the commanded switch, **not** measured voltage/current.
Another power feed or a bypass jumper may keep the DUT powered. The standard
streaming power `read()` operation is unsupported.

Raw GPIO commands do not require a HAT. They affect whatever is wired to the
selected managed line; GPIO1 is not inherently a power button or relay channel:

```shell
j copilot set 1 on
j copilot set 1 off
j copilot pulse 1 --duration 1
```

Pulse durations are finite values from 1 to 30 seconds. The complete pulse runs
on the exporter. Direct pulses and GPIO access are serialized; callers must
avoid unrelated concurrent commands during external multi-output sequences.

## Board serial console and logs

The CoPilot UART is available without a HAT. Install
`jumpstarter-driver-pyserial` alongside this package and add this sibling export:

```yaml
export:
  serial:
    type: jumpstarter_driver_pyserial.driver.PySerial
    config:
      url: /dev/copilot/by-id/YOUR_COPILOT_SERIAL/tty
      baudrate: 115200
```

Merge this entry into the existing `export` mapping; the complete configuration
is in `examples/exporter-uart.yaml`. Both serial placeholders must identify the
same unit. Connect the UART to the DUT and choose its voltage and baud rate using
the [CoPilot UART setup guide](https://baylibre.github.io/Copilot/getting-started.html#connect-to-an-uart).
The exporter account needs access to the tty as well as the GPIO device.

```shell
j serial console
# start-console is also accepted as an alias
j serial pipe --no-input | tee serial.log
```

Exit the interactive console with Ctrl+B three times. The log file is written
on the client. PySerial owns serial transport and its lifecycle; GPIO reconnect
handling does not implement UART recovery. See the
[PySerial driver documentation](https://jumpstarter.dev/main/reference/package-apis/drivers/pyserial.html).

## Triple-relay HAT

The [BayLibre triple-relay HAT](https://github.com/BayLibre/Copilot_Extension_Triple_Relays)
provides three signal relays. Its jumpers select the GPIO driving each relay.
Add the following under the controller's `config`, matching those jumpers:

```yaml
managed_lines: [0, 1, 2, 3]
hats:
  - type: triple-relay
    relay_lines: [1, 2, 3]
```

Only with this HAT description configured, use:

```shell
j copilot relay 1 on
j copilot relay 1 off
j copilot read-relay 1
j copilot pulse-relay 1 --duration 1
```

Here `relay 1` selects the **first entry in `relay_lines`**, whereas `set 1`
selects **GPIO1**. They refer to the same line only with a matching mapping.
`read-relay` reads the GPIO command, not the physical contacts. The driver cannot
detect whether the declared HAT is actually fitted.

### Named contacts in the console

A named output is configured explicitly; `reset` and `buttons` are not built-in
CoPilot roles. For a relay on GPIO3 wired to a DUT reset contact, add:

```yaml
export:
  buttons:
    type: jumpstarter_driver_composite.driver.Composite
    children:
      reset:
        type: jumpstarter_driver_copilot.driver.CoPilotOutput
        config:
          line: 3
        children:
          controller:
            ref: copilot
```

Then `j buttons reset on` asserts the contact and `j buttons reset off` releases
it. A child named `power` would similarly expose `j buttons power on/off`; that
contact is distinct from the GPIO0 native supply export `j power on/off`.
`Composite` groups and names the child commands; it does not add pulse timing or
collective release. No Buttons package is required.

The full relay example defines all three contacts under `buttons` and a
top-level `reset` reference to `buttons.reset`. Thus `j reset on/off` and
`j buttons reset on/off` operate the same output. These names come from YAML. `CoPilotOutput` can expose
another managed output without a
relay HAT, provided its wiring and electrical interface suit the target.
All adapters share the controller's GPIO request.

## Triple-USB HAT

The [BayLibre triple-USB HAT](https://github.com/BayLibre/Copilot-extension-triple-usb-2.0)
has three USB 2.0 passthrough channels whose power is controlled by GPIOs selected
through jumpers. This is not a software-controlled USB hub protocol: the driver
switches the channel power line, it does not enumerate USB devices.

Describe the jumper selection in port order and add a `CoPilotUSB` export:

```yaml
export:
  copilot:
    type: jumpstarter_driver_copilot.driver.CoPilotGPIO
    config:
      serial: YOUR_COPILOT_SERIAL
      managed_lines: [0, 1, 2, 3]
      hats:
        - type: usb-extension
          ports: 3
          usb_lines: [1, 2, 3]   # port 1 -> GPIO1, port 2 -> GPIO2, port 3 -> GPIO3
  usb:
    type: jumpstarter_driver_copilot.driver.CoPilotUSB
    children:
      controller:
        ref: copilot
```

Ports are numbered from 1; port N drives `usb_lines[N - 1]`. Inside a
Jumpstarter shell:

```shell
j usb 1 on
j usb 1 off
j usb 1 read
j usb 1 cycle --wait 2
```

`cycle` switches the port off, waits (1 to 30 seconds), then switches it on
again, so the attached device re-enumerates; the whole sequence runs on the
exporter and the port ends enabled. `read` reports the GPIO command, not the
VBUS voltage or the presence of a device. With `ports` and `usb_lines` both
set, their counts must match. `usb_lines` must not overlap a `triple-relay`
`relay_lines`, and only one `usb-extension` entry may define it. Without
`usb_lines`, the HAT is inventory metadata and `CoPilotUSB` commands fail.

All managed lines share one `active_low` setting, and native supply on GPIO0
requires `false`. Per-port on, off and cycle were checked on a CoPilot Lite V1.2
with the triple-USB HAT; verify the port-to-GPIO mapping against your jumpers.

### Multiple accessories

The hardware permits stacking extensions. GPIO assignments must match the
physical wiring: two accessories connected to the same GPIO will be controlled
together. Adding multiple `hats` entries does not allocate independent GPIOs or
validate electrical conflicts. Account for GPIO0's native supply function as
well. The driver currently allows only one `triple-relay` description.

## USB loss and cleanup

The selected serial stays pinned when a unit was initially selected automatically.
The next operation detects a changed node or stale request and attempts to
reacquire the same unit with every output inactive. Writes are never replayed.
If a line was asserted when the connection was lost, subsequent assertions are
blocked until the interrupted lines are released or explicitly acknowledged:

```shell
j copilot reconnect
```

This command releases every managed output, including supply GPIO0. Use it only
when no action is running. Releasing an interrupted line reports the interruption
once even if cleanup succeeded. A pulse interrupted by USB loss fails.
`info()` reports connection status, generation, interrupted lines and the last
error. Status queries may reconnect and therefore initialize outputs inactive.

Session teardown releases outputs and GPIO descriptors. Shared references can
close the controller repeatedly; cleanup is idempotent. Reconnection and cleanup
cannot guarantee physical relay or supply behavior while USB is disconnected.

## API reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_copilot.driver.CoPilotGPIO()
   :members:

.. autoclass:: jumpstarter_driver_copilot.driver.CoPilotOutput()
   :members:

.. autoclass:: jumpstarter_driver_copilot.driver.CoPilotPower()
   :members:

.. autoclass:: jumpstarter_driver_copilot.driver.CoPilotUSB()
   :members:
```
