# ESP32 driver

`jumpstarter-driver-esp32` provides functionality for flashing, monitoring, and controlling ESP32 devices using esptool and serial communication.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-esp32
```

## Configuration

Example configuration:

```yaml
export:
  esp32:
    type: jumpstarter_driver_esp32.driver.ESP32
    config:
      port: "/dev/ttyUSB0"
      baudrate: 115200
      chip: "esp32"
      # Optional GPIO pins for hardware control
      # reset_pin: 2
      # boot_pin: 0
```

### Config parameters

| Parameter     | Description                                                           | Type | Required | Default |
| ------------- | --------------------------------------------------------------------- | ---- | -------- | ------- |
| port          | The serial port to connect to the ESP32                              | str  | yes      |         |
| baudrate      | The baudrate for serial communication                                | int  | no       | 115200  |
| chip          | The ESP32 chip type (esp32, esp32s2, esp32s3, esp32c3, etc.)        | str  | no       | esp32   |
| reset_pin     | GPIO pin number for hardware reset (if connected)                    | int  | no       | null    |
| boot_pin      | GPIO pin number for boot mode control (if connected)                 | int  | no       | null    |
| check_present | Check if the serial port exists during exporter initialization       | bool | no       | True    |

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_esp32.client.ESP32Client()
    :members: chip_info, reset, erase_flash, flash_firmware, flash_firmware_file, read_flash, enter_bootloader
```

### CLI

The ESP32 driver client comes with a CLI tool that can be used to interact with ESP32 devices:

```
jumpstarter ⚡ local ➤ j esp32
Usage: j esp32 [OPTIONS] COMMAND [ARGS]...

  ESP32 client

Options:
  --help  Show this message and exit.

Commands:
  bootloader   Enter bootloader mode
  chip-id      Get chip ID information
  erase        Erase the entire flash
  flash        Flash firmware to the device
  info         Get device information
  read-flash   Read flash contents
  reset        Reset the device
```

## Examples

### Getting device information

```{testcode}
info = esp32.chip_info()
print(f"Connected to {info['chip_revision']}")
print(f"MAC Address: {info['mac_address']}")
print(f"Chip ID: {info['chip_id']}")
```

### Flashing firmware

```{testcode}
# Flash firmware from a local file
result = esp32.flash_firmware_file("firmware.bin", address=0x10000)
print(result)

# Flash firmware using OpenDAL operator
from opendal import Operator
operator = Operator("fs", root="/path/to/firmware")
result = esp32.flash_firmware(operator, "firmware.bin", address=0x10000)
print(result)
```

### Reading flash contents

```{testcode}
# Read 1024 bytes from address 0x0
data = esp32.read_flash(address=0x0, size=1024)
print(f"Read {len(data)} bytes from flash")
```

### Device control

```{testcode}
# Reset the device
result = esp32.reset()
print(result)

# Enter bootloader mode
result = esp32.enter_bootloader()
print(result)

# Erase entire flash (use with caution!)
result = esp32.erase_flash()
print(result)
```

### CLI Examples

```{code-block} console
# Get device information
$ j esp32 info

# Flash firmware to default app partition (0x10000)
$ j esp32 flash firmware.bin

# Flash firmware to specific address
$ j esp32 flash firmware.bin --address 0x1000

# Read flash contents
$ j esp32 read-flash 0x0 1024

# Save flash contents to file
$ j esp32 read-flash 0x0 1024 --output flash_dump.bin

# Reset the device
$ j esp32 reset

# Erase the entire flash
$ j esp32 erase
```

```{testsetup} *
from jumpstarter_driver_esp32.driver import ESP32
from jumpstarter.common.utils import serve

instance = serve(ESP32(port="loop://"))

esp32 = instance.__enter__()
```

```{testcleanup} *
instance.__exit__(None, None, None)
```
