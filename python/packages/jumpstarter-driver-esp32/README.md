# ESP32 driver

`jumpstarter-driver-esp32` provides functionality for flashing and managing
ESP32 devices using [esptool](https://github.com/espressif/esptool) as a
library. It implements the `FlasherInterface` from `jumpstarter-driver-opendal`.

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
    type: jumpstarter_driver_esp32.driver.Esp32Flasher
    config:
      port: "/dev/ttyUSB0"
      baudrate: 460800
  serial:
    type: jumpstarter_driver_pyserial.driver.PySerial
    config:
      url: "/dev/ttyUSB0"
      baudrate: 115200
```

### Config parameters

| Parameter | Description                          | Type | Required | Default       |
| --------- | ------------------------------------ | ---- | -------- | ------------- |
| port      | Serial port for the ESP32 device     | str  | no       | /dev/ttyUSB0  |
| baudrate  | Baud rate for esptool communication  | int  | no       | 115200        |

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_esp32.client.Esp32FlasherClient()
    :members: flash, dump, get_chip_info, erase, hard_reset, enter_bootloader
```

### CLI

The ESP32 driver client inherits flash/dump CLI commands from the
`FlasherClient` and adds ESP32-specific commands:

```
jumpstarter ⚡ local ➤ j esp32
Usage: j esp32 [OPTIONS] COMMAND [ARGS]...

Options:
  --help  Show this message and exit.

Commands:
  bootloader  Enter download mode
  chip-info   Get chip info (name, features, MAC)
  dump        Dump flash content to file
  erase       Erase entire flash
  flash       Flash firmware to device
  reset       Hard reset the chip
```

## Examples

### Get chip information

```python
info = esp32_client.get_chip_info()
print(info["chip"])      # e.g. "ESP32-D0WD-V3 (revision v3.1)"
print(info["features"])  # e.g. "Wi-Fi, BT, Dual Core"
print(info["mac"])       # e.g. "5c:01:3b:68:ab:0c"
```

### Flash firmware

```python
# Flash to default address (0x0)
esp32_client.flash("/path/to/firmware.bin")

# Flash to specific address
esp32_client.flash("/path/to/firmware.bin", target="0x10000")
```

### Dump flash contents

```python
# Dump 4MB from address 0x0 (default)
esp32_client.dump("/path/to/output.bin")

# Dump specific region (address:size)
esp32_client.dump("/path/to/output.bin", target="0x10000:0x1000")
```

### Erase flash

```python
esp32_client.erase()
```

### Hard reset

```python
esp32_client.hard_reset()
```
