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

# Read current power state
state = power_switch.read()
print(f"Power state: {state}")
```

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
feeds a device's power. The kernel keeps a released line at its last level, so
a restart then leaves the load alone. Before anything has claimed the line
(cold boot), it reads whatever its bias or float gives. To make that level
deterministic, set it in firmware, e.g. `gpio=26=op,dh` in `/boot/firmware/config.txt`.

#### Status

`status` returns `on` or `off` from the logical level the Pi drives,
`active_low` already applied. It cannot tell whether a relay behind the line
actually switched: a missing jumper, a lost supply, or a welded contact all
still report the driven level.

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
