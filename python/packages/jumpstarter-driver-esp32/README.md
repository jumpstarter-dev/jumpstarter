# ESP32 Driver

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

```{literalinclude} ../../../../../packages/jumpstarter-driver-esp32/examples/config.yaml
:language: yaml
```

### Config parameters

| Parameter | Description                          | Type | Required | Default       |
| --------- | ------------------------------------ | ---- | -------- | ------------- |
| baudrate  | Baud rate for esptool communication  | int  | no       | 115200        |
| chip      | Target chip type                     | str  | no       | esp32         |

The ESP32 driver requires a `serial` child driver (PySerial) for serial port
access. DTR/RTS control signals and the serial port path are managed through
the child driver. Use a `ref` proxy to share the serial driver with the
top-level composite, enabling both `j serial start-console` and
`j storage flash` to work.

## Usage

### CLI

```text
$ j storage
Usage: j storage [OPTIONS] COMMAND [ARGS]...

Commands:
  bootloader  Enter download mode
  chip-info   Get chip info (name, features, MAC)
  dump        Dump flash content to file
  erase       Erase entire flash
  flash       Flash firmware to ESP32
  reset       Hard reset the chip

$ j serial
Usage: j serial [OPTIONS] COMMAND [ARGS]...

Commands:
  start-console  Start serial port console
  pipe           Pipe serial port data to stdout or file
```

### CLI usage

```bash
# Flash MicroPython firmware
j storage flash firmware.bin --address 0x1000

# Get chip info
j storage chip-info

# Enter download mode
j storage bootloader

# Erase entire flash
j storage erase

# Hard reset
j storage reset

# Open serial console
j serial start-console

# Read serial output
j serial pipe
```

### Python API

```{literalinclude} ../../../../../packages/jumpstarter-driver-esp32/examples/usage.py
:language: python
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_esp32.client.Esp32FlasherClient()
    :members: flash, dump, get_chip_info, erase, hard_reset, enter_bootloader
```
