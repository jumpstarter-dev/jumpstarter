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

Example configuration for power switching:

```yaml
export:
  power_switch:
    type: jumpstarter_driver_gpiod.driver.PowerSwitch
    config:
      line: 18
      drive: "push_pull"
      active_low: false
      bias: "pull_up"
      initial_value: "preserve"
```

`PowerSwitch` speaks the same `PowerInterface` as the other relay drivers
(`j power_switch on|off|cycle|status`). `read` raises `NotImplementedError`,
because a GPIO-switched contact has no voltage or current to measure.

### Config parameters

| Parameter      | Description                                                                                                                                          | Type | Required | Default | Driver Types |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ---- | -------- | ------- | ------------ |
| device         | The GPIO device to use (can be integer or string like "/dev/gpiochip0")                                                                            | str | no | "/dev/gpiochip0" | All |
| line            | The GPIO line number to use                                                                              | int | yes | | All |
| drive          | The drive mode for the GPIO line. Options: "push_pull", "open_drain", "open_source"                                                                 | str | no | null | DigitalOutput, PowerSwitch |
| active_low     | Whether the pin is active low (True) or active high (False)                                                                                         | bool | no | False | All |
| bias           | The bias configuration for the GPIO line. Options: "as_is", "pull_up", "pull_down", "disabled"                                                      | str | no | null | All |
| initial_value  | The initial value for output pins. Options: "active", "inactive", "on", "off", "preserve", True, False. "preserve" claims the line without changing its level, see below | str/bool | no | "inactive" | DigitalOutput, PowerSwitch |

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

# Read current power state: "on", "off", or "unknown" (best-effort, see Status)
state = power_switch.status()
print(f"Power state: {state}")
```

`read()` keeps `PowerInterface`'s meaning (a stream of power measurements), which
a dry-contact relay cannot provide, so it raises `NotImplementedError` here. Use
`status()` for the switch state.

### Pin Configuration Details

#### Drive Modes

- **push_pull**: Standard push-pull output (default)
- **open_drain**: Open-drain output (useful for I2C, etc.)
- **open_source**: Open-source output

#### Bias Configuration

- **as_is**: No bias (default)
- **pull_up**: Internal pull-up resistor
- **pull_down**: Internal pull-down resistor
- **disabled**: Disable bias

#### Active Low vs Active High

- **active_low: false** (default): Pin is active when HIGH
- **active_low: true**: Pin is active when LOW

#### Initial Values

For output pins, you can set the initial state:
- **"inactive"** or **"off"** or **False**: Start with pin LOW
- **"active"** or **"on"** or **True**: Start with pin HIGH
- **"preserve"**: Keep whatever level the line is already at

Any other value drives the line when the exporter starts, so every exporter
restart switches whatever the line controls. Use `preserve` for a line that
feeds a device's power: it reads the line's level before configuring it as an
output, then drives that same level.

`preserve` narrows the window for a transition, it does not close it. The kernel
makes no promise about a line once its request is released — the level is then up
to the GPIO controller and the pin's bias, and it may have moved by the time
`preserve` reclaims the line and reads it. Before anything has claimed the line
at all (cold boot), it reads whatever its bias or float gives.

So where the load must not switch across an exporter restart, hold the level
outside the request: a latching relay, an external pull that matches the wanted
state, or a firmware pin setting — e.g. `gpio=26=op,dh` in
`/boot/firmware/config.txt`, which also makes the cold-boot level deterministic.

#### Status

`status` is **best-effort**. It is derived from the configured settings (`drive`,
`active_low`, `initial_value`) and the line's readback — never from the load
itself — and returns one of:

- **`on`** / **`off`**: the level this driver last drove the line to, by `on()`,
  `off()`, or the initial request, with `active_low` already applied, and nothing
  has contradicted it since.
- **`unknown`**: the driver can no longer vouch for the level. This happens when a
  write fails, when the readback fails, or when a `push_pull` line reads back
  something other than what was driven — a shorted pin, a dead pad, or a load
  dragging the line past the logic threshold. A mismatch is logged as a warning.

`unknown` is not permanent: calling `on()` or `off()` drives the line again and,
if the write takes, makes the state known. Treat it as a cue to set the state
explicitly rather than assume it.

The readback check only applies to `push_pull` (the default). An `open_drain` or
`open_source` line leaves one of its levels high-impedance, where the pad follows
the external pull instead of the driver, so its readback cannot confirm or refute
what was driven; `status` then simply reports the level last driven. `read` (on
`DigitalOutputClient`) still gives you the raw pin read for any drive mode.

With `initial_value: preserve`, the starting state is the level the line was
found at, adopted as-is. `status` reports it accurately as the level now driven,
but cannot tell whether that level was ever intended or is a reset default — see
[Initial Values](#initial-values).

No value of `status` can tell whether a relay behind the line actually switched:
a missing jumper, a lost supply, or a welded contact all still report the driven
level. Only a feedback path wired back to the exporter can confirm that.

## API Reference

### DigitalOutputClient

```{eval-rst}
.. autoclass:: jumpstarter_driver_gpiod.client.DigitalOutputClient()
    :members: on, off, read, status
```

### PowerSwitchClient

```{eval-rst}
.. autoclass:: jumpstarter_driver_gpiod.client.PowerSwitchClient()
    :members: on, off, cycle, status
```

### DigitalInputClient

```{eval-rst}
.. autoclass:: jumpstarter_driver_gpiod.client.DigitalInputClient()
    :members: wait_for_active, wait_for_inactive, wait_for_edge, read
```

- Timeout conditions for input operations
