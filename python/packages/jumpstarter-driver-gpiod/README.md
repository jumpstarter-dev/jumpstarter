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

```{literalinclude} ../../../../../packages/jumpstarter-driver-gpiod/examples/config.yaml
:language: yaml
```

### DigitalInput Configuration

Example configuration for digital input:

```{literalinclude} ../../../../../packages/jumpstarter-driver-gpiod/examples/config_digital_input.yaml
:language: yaml
```

### PowerSwitch Configuration

Example configuration for power switching:

```{literalinclude} ../../../../../packages/jumpstarter-driver-gpiod/examples/config_power_switch.yaml
:language: yaml
```

### Config parameters

| Parameter      | Description                                                                                                                                          | Type | Required | Default | Driver Types |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ---- | -------- | ------- | ------------ |
| device         | The GPIO device to use (can be integer or string like "/dev/gpiochip0")                                                                            | str | no | "/dev/gpiochip0" | All |
| line            | The GPIO line number to use                                                                              | int | yes | | All |
| drive          | The drive mode for the GPIO line. Options: "push_pull", "open_drain", "open_source"                                                                 | str | no | null | DigitalOutput, PowerSwitch |
| active_low     | Whether the pin is active low (True) or active high (False)                                                                                         | bool | no | False | All |
| bias           | The bias configuration for the GPIO line. Options: "as_is", "pull_up", "pull_down", "disabled"                                                      | str | no | null | All |
| initial_value  | The initial value for output pins. Options: "active", "inactive", "on", "off", True, False                                                          | str/bool | no | "inactive" | DigitalOutput, PowerSwitch |
| mode           | The mode for PowerSwitch (same as drive parameter)                                                                                                   | str | no | "push_pull" | PowerSwitch |

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

## API Reference

### DigitalOutputClient

```{eval-rst}
.. autoclass:: jumpstarter_driver_gpiod.client.DigitalOutputClient()
    :members: on, off, read
```

### DigitalInputClient

```{eval-rst}
.. autoclass:: jumpstarter_driver_gpiod.client.DigitalInputClient()
    :members: wait_for_active, wait_for_inactive, wait_for_edge, read
```

- Timeout conditions for input operations
