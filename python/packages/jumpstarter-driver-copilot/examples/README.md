# CoPilot bench examples

These configurations compose CoPilot with existing Jumpstarter drivers. The
named contacts use Composite; no Buttons driver is required.

| Configuration | Hardware and exported controls |
| --- | --- |
| `exporter-base.yaml` | Bare CoPilot: native DUT supply |
| `exporter-uart.yaml` | Bare CoPilot: supply and UART console/log capture |
| `exporter.yaml` | Triple-relay HAT: supply, Power/Recovery/Reset contacts |
| `exporter-relay-uart.yaml` | Full relay bench with UART capture |
| `exporter-usb.yaml` | Triple-USB HAT: supply and per-port USB power control |

## Prepare the exporter

Install `jumpstarter-driver-copilot`, and also `jumpstarter-driver-pyserial` for
UART examples. Composite is included as a dependency. Set up the BayLibre udev
rules and give the exporter access to the GPIO device and, when used, the tty.
Replace `YOUR_COPILOT_SERIAL` in both the GPIO configuration and the UART path.
Set the UART voltage on the hardware and choose the DUT's baud rate.

For a local session, run from this directory on the exporter host:

```shell
jmp shell --exporter-config ./exporter-relay-uart.yaml
```

Local mode does not use the example controller endpoint/token. For a managed
exporter, replace those settings with your controller connection information.
Do not run two exporters against the same GPIO lines.

The examples initialize outputs inactive. Native supply control requires J1 to
be set to GPIO control. `j power state` reads GPIO0; it does not measure voltage.

## Wire the relay bench

The full example assumes this wiring. Check the HAT jumpers against the actual
board before using it.

| CoPilot control | Connection | Named command |
| --- | --- | --- |
| GPIO0 | Native DUT supply path | `j power on/off` |
| GPIO1 / relay 1 | Across the DUT Power contact | `j buttons power on/off` |
| GPIO2 / relay 2 | Across the DUT Recovery contact | `j buttons recovery on/off` |
| GPIO3 / relay 3 | Across the DUT Reset contact | `j buttons reset on/off` |
| UART | TX to DUT RX, RX to DUT TX, common GND | `j serial console` |

These contact names describe this bench; the driver does not detect the DUT's
button layout. Follow the hardware documentation for relay contact ratings and
UART voltage. If the jumper mapping changes, update both `relay_lines` and the
corresponding `CoPilotOutput.line` values. The top-level `reset` reference is a
shortcut to `buttons.reset`, sharing the same output and GPIO owner.

## Capture a cold boot

Inside a UART-enabled session, attach the log reader before powering the DUT:

```shell
j serial pipe --no-input -o boot.log &
capture_pid=$!
j serial console-status
```

Check that the reader is attached before continuing; starting a background
process alone does not establish readiness. With `always_on: false`, UART opens
when a client attaches, so earlier boot output cannot be recovered. The log file
is written on the client running the command.

```shell
j power on
j power state
```

If the DUT also needs a Power contact press and relay 1 is wired as above:

```shell
j copilot pulse-relay 1 --duration 1
```

This pulses the contact for one second on the exporter. It does not cycle the
native supply. For an interactive console, use `j serial console` (also exposed
as `j serial start-console`); press Ctrl+B three times to exit.

Stop the background capture when finished:

```shell
kill "$capture_pid"
wait "$capture_pid"
```

Termination may produce a nonzero exit status. Stopping capture does not power
off the DUT.

## Hold contacts and combine them

A named output stays asserted until released:

```shell
j buttons power on
j buttons power off
```

For a timed Reset press, use the controller's server-side pulse:

```shell
j copilot pulse-relay 3 --duration 1
```

For a DUT that enters recovery by holding Recovery while Reset is pulsed, the
example wiring allows:

```shell
j buttons recovery on
j copilot pulse-relay 3 --duration 1
j buttons recovery off
```

This is an illustrative sequence, not a universal bootloader recipe. Use the
DUT's required order and timings. If a command fails, release held contacts;
automation should do so in its cleanup path. Composite adds names, not a
`pulse` or `release-all` operation. `j copilot pulse 3 --duration 1` addresses
GPIO3 directly; `pulse-relay 3` instead resolves relay channel 3 through
`relay_lines`.

At the end of the session, release contacts and optionally remove native power:

```shell
j buttons power off
j buttons recovery off
j buttons reset off
j power off
```

Do not rely on closing the client connection to switch off the supply.

## USB HAT ports

`exporter-usb.yaml` maps ports 1-3 to GPIO1-3 through `usb_lines` and exports
them as `usb`. Adjust `usb_lines` to the jumper positions of your HAT, and check
that no other stacked accessory uses the same GPIOs.

```shell
j usb 2 on                # enable the device on port 2
j usb 2 cycle --wait 3    # simulate an unplug/replug of that device
j usb 2 off
```

As with the supply, closing the client connection does not switch ports off.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| GPIO device or tty missing | Serial number, USB connection, udev rules and permissions |
| GPIO already requested | Another exporter or process owns the same lines |
| Relay command rejected | `triple-relay` declaration and three managed `relay_lines` |
| `buttons` command missing | The base/UART-only examples do not export relay contacts |
| Supply does not follow GPIO0 | J1 selection, supply wiring and other DUT power sources |
| No UART log | tty path, crossed TX/RX, ground, voltage, baud rate and reader readiness |
| Assertions blocked after USB loss | Release/acknowledge interrupted lines before starting a new action |

GPIO reconnection initializes managed outputs inactive, including native supply.
It does not replay a contact sequence, restore DUT power or repair a UART
session automatically.
