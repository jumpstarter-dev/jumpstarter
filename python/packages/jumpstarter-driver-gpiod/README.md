# gpiod Driver

`jumpstarter-driver-gpiod` provides functionality for interacting with
gpiod GPIO pins for digital input/output operations.

This requires the /dev/gpiochip[0..N] device available on the system, and you can use the `gpioinfo` gpiod tool to list the available GPIO lines.


## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-gpiod
```

### Hardware Requirements

- gpiod with GPIO access
- Python `gpiod` library installed
- Appropriate permissions to access `/dev/gpiochip0`

## Configuration

The gpiod driver provides three main driver types:

### DigitalOutput Configuration

Example configuration for digital output:

```yaml
export:
  led_output:
    type: jumpstarter_driver_gpiod.driver.DigitalOutput
    config:
      device: "/dev/gpiochip0"
      line: 18
      drive: "push_pull"
      active_low: false
      bias: "pull_up"
      initial_value: "inactive"
```

### DigitalInput Configuration

Example configuration for digital input:

```yaml
export:
  button_input:
    type: jumpstarter_driver_gpiod.driver.DigitalInput
    config:
      line: 17
      active_low: false
      bias: "pull_up"
```

### PowerSwitch Configuration

Example configuration for one channel of a GPIO relay HAT:

```yaml
export:
  power:
    type: jumpstarter_driver_gpiod.driver.PowerSwitch
    config:
      line: 26
      drive: push_pull        # or open_drain / open_source, as the board needs
      active_low: true        # most relay HATs energize the coil on a LOW input
      initial_value: preserve # an exporter restart leaves the relay where it is
```

Set `active_low` to match the board, so that `on` means "relay energized". Use one
`PowerSwitch` per channel.

`PowerSwitch` speaks the same `PowerInterface` as the other relay drivers
(`j power on|off|cycle|status`). `read` raises `NotImplementedError`, because a
GPIO-switched contact has no voltage or current to measure; use `status` for the
switch state. See [Power relays](#power-relays) for what survives a restart and
what doesn't.

### Config parameters

| Parameter      | Description                                                                                                                                          | Type | Required | Default | Driver Types |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ---- | -------- | ------- | ------------ |
| device         | The GPIO device to use (can be integer or string like "/dev/gpiochip0")                                                                            | str | no | "/dev/gpiochip0" | All |
| line            | The GPIO line number to use                                                                              | int | yes | | All |
| drive          | The drive mode for the GPIO line. Options: "push_pull", "open_drain", "open_source". Unset means "push_pull"                                      | str | no | null | DigitalOutput, PowerSwitch |
| active_low     | Whether the pin is active low (True) or active high (False)                                                                                         | bool | no | False | All |
| bias           | The bias configuration for the GPIO line. Options: "as_is", "pull_up", "pull_down", "disabled"                                                      | str | no | null | All |
| initial_value  | The initial value for output pins. Options: "active", "inactive", "on", "off", "preserve", True, False. "preserve" claims the line without changing its level, see below | str/bool | no | "inactive" | DigitalOutput, PowerSwitch |
| mode           | Not used; the drive mode comes from `drive`. Accepted so existing configs load                                                                   | str | no | "push_pull" | PowerSwitch |

## Usage

### Digital Output Examples

Basic LED control:
```
# Turn LED on
led_output.on()

# Turn LED off
led_output.off()

# Read current state
state = led_output.read()
print(f"LED state: {state}")
```

### Digital Input Examples

Button input with edge detection:
```
# Read current input state
state = button_input.read()
print(f"Button state: {state}")

# Wait for button press (active state)
button_input.wait_for_active(timeout=10.0)

# Wait for button release (inactive state)
button_input.wait_for_inactive(timeout=10.0)

# Wait for rising edge (button press)
button_input.wait_for_edge("rising", timeout=10.0)

# Wait for falling edge (button release)
button_input.wait_for_edge("falling", timeout=10.0)
```


### Power Switch Examples

Power control for devices:
```
# Turn power on
power_switch.on()

# Turn power off
power_switch.off()

# Read current power state: "on", "off", or "unknown" (the level last commanded, see Status)
state = power_switch.status()
print(f"Power state: {state}")
```

### Pin Configuration Details

#### Drive Modes

`drive` sets how the pin drives each level. It applies to `DigitalOutput` and
`PowerSwitch`.

- **push_pull** (default): drives both HIGH and LOW. Use it when the pin alone
  sets the input it is wired to, as with most relay HATs.
- **open_drain**: drives LOW, and lets go of the line (high impedance) for HIGH.
  Use it when a pull-up on the far side sets the HIGH level: a reset or power
  button line on a DUT, a line at a different voltage than the Pi's 3.3 V, or a
  line other devices can also pull LOW.
- **open_source**: the mirror image: drives HIGH, and lets go for LOW, which a
  pull-down on the far side then sets.

With `open_drain` or `open_source`, the released level comes from the pull (on
the board, or from `bias`), not from the pin.

For example, a power control line driven open-drain. With `active_low: false`,
`on` releases the line (the pull-up takes it HIGH) and `off` pulls it LOW:

```yaml
export:
  power:
    type: jumpstarter_driver_gpiod.driver.DigitalOutput
    config:
      line: 23
      drive: open_drain
      initial_value: "on"
```

#### Bias Configuration

`bias` enables the SoC's internal pull resistor on the pin. It applies to all
driver types.

- **as_is** (default): leave the bias as it is already configured
- **pull_up**: internal pull-up resistor
- **pull_down**: internal pull-down resistor
- **disabled**: no pull resistor

On an input, the pull sets the level when nothing drives the line (a button
wired to ground needs `pull_up`). On an `open_drain` or `open_source` output, it
can supply the released level when the far side has no pull of its own. Internal
pulls are weak (tens of kΩ on a Raspberry Pi), so use an external resistor for
long wires or fast edges.

#### Active Low vs Active High

- **active_low: false** (default): Pin is active when HIGH
- **active_low: true**: Pin is active when LOW

#### Initial Values

For output pins, you can set the initial state:
- **"inactive"** or **"off"** or **False**: Start inactive
- **"active"** or **"on"** or **True**: Start active
- **"preserve"**: Keep whatever level the line is already at

`active_low` is applied first, so "active" means the pin is LOW when `active_low`
is set. Any value other than `preserve` drives the line every time the exporter
starts.

#### Status

`status` returns `on`, `off`, or `unknown`: the level this driver last commanded,
not a measurement.

- **`on`** / **`off`**: the level last driven (by `on()`, `off()`, or the initial
  request), with `active_low` applied.
- **`unknown`**: the last write raised. The next `on()` or `off()` that succeeds
  makes the state known again.

`status` does not read the line back. Reading an output line can return either its
output or its input buffer, depending on the controller, so a readback can differ
from what was driven without meaning anything is wrong. A shorted pin or dead pad
therefore still reports the commanded level. `read` on `DigitalOutputClient` gives
the raw pin read when you want it.

### Power relays

A GPIO relay HAT has one input per channel, and a pull resistor on the board holds
each input at its de-energized level whenever nothing drives it. `PowerSwitch`
drives one channel. What the relay does across restarts is set by the board and the
host, not by this driver:

| Event                                        | Relay                                  | `status` afterwards           |
| -------------------------------------------- | -------------------------------------- | ----------------------------- |
| Exporter restart, with `initial_value: preserve` | Unchanged                          | The level it was left at      |
| Exporter restart, with any other `initial_value` | Driven to that value               | That value                    |
| Host reboot                                  | **De-energizes** for the whole boot    | The level found at start      |
| Host power loss                              | De-energizes                           | The level found at start      |

- **Exporter restart.** When the exporter exits, cleanly or by crashing, the kernel
  releases the line, and it keeps its level on typical SoC controllers (verified on
  a Raspberry Pi 4, including after SIGKILL, with no glitch on the line).
  `preserve` reads that level back and keeps driving it, so the relay doesn't move.
  The kernel doesn't guarantee this for every controller, so check yours if it
  matters.
- **Host reboot.** A reboot resets the SoC, which returns every GPIO to an input.
  The board's pull then de-energizes the relay until something claims the line
  again, and the previous level is gone. No software setting prevents this: on a
  Raspberry Pi, `gpio=` in `config.txt` only takes effect after the reset, so the
  relay drops and then re-energizes, and `cmdline.txt` runs later still. After a
  reboot, `preserve` finds the de-energized level and reports it.

So **wire each load for the state you want during a reboot**. The relay is
de-energized at reset, during boot, and with the host off:

- For a load that should stay **powered** through a host reboot (a DUT's supply),
  wire it through the relay's **NC** (normally closed) contact. `on` then means
  *de-energize the relay*, so invert `active_low` from what NO wiring would use:
  on a board that energizes on LOW, set `active_low: false`. The load is then
  powered at reset and during boot, and `off` energizes the relay to cut it.
- For a load that should be **off** unless commanded (ignition, a button press),
  wire it through the **NO** (normally open) contact.
- If the load must keep *either* state through a host reboot, use a latching
  relay. A standard relay can't do this.

Neither `status` nor anything else in this driver can tell whether a relay
actually switched: a missing jumper, a lost coil supply, or a welded contact all
still report the driven level. Only a feedback line wired back to the host can
confirm it.

## API Reference

### DigitalOutputClient

```{eval-rst}
.. autoclass:: jumpstarter_driver_gpiod.client.DigitalOutputClient()
    :members: on, off, read, status
```

`PowerSwitch` uses the standard `jumpstarter_driver_power.client.PowerClient`
(`on`, `off`, `cycle`, `status`).

`jumpstarter_driver_gpiod.client.PowerSwitchClient` is **deprecated**. It is kept
so existing imports keep working, adds nothing to `PowerClient`, and emits a
`DeprecationWarning` when created. Use `PowerClient` instead.

### DigitalInputClient

```{eval-rst}
.. autoclass:: jumpstarter_driver_gpiod.client.DigitalInputClient()
    :members: wait_for_active, wait_for_inactive, wait_for_edge, read
```

- Timeout conditions for input operations
