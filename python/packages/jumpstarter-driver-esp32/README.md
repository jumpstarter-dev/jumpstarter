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

```yaml
export:
  storage:
    type: jumpstarter_driver_esp32.driver.Esp32Flasher
    config:
      baudrate: 115200
      chip: "esp32"
    children:
      serial:
        ref: serial
  serial:
    type: jumpstarter_driver_pyserial.driver.PySerial
    config:
      url: "/dev/ttyUSB0"
      baudrate: 115200
```

### Config parameters

| Parameter      | Description                                                        | Type | Required | Default |
| -------------- | ------------------------------------------------------------------ | ---- | -------- | ------- |
| baudrate       | Baud rate for connecting to the ROM bootloader                     | int  | no       | 115200  |
| flash_baudrate | Baud rate once the flasher stub runs; at or below `baudrate`, kept | int  | no       | 460800  |
| chip           | Target chip type                                                   | str  | no       | esp32   |

Flash, dump and erase switch to `flash_baudrate` after loading esptool's
flasher stub, as `esptool.py` and `idf.py` do. Lower it if a USB-serial bridge
or cable is unreliable at that speed. The switch only happens when
`flash_baudrate` is above `baudrate`; to go slower than `baudrate`, lower
`baudrate` itself.

The ESP32 driver requires a `serial` child driver (PySerial) for serial port
access. DTR/RTS control signals and the serial port path are managed through
the child driver. Use a `ref` proxy to share the serial driver with the
top-level composite, enabling both `j serial console` and
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
  console        Start serial port console
  pipe           Pipe serial port data to stdout or file
```

### CLI usage

```bash
# Flash MicroPython firmware
j storage flash firmware.bin --address 0x1000

# Flash an ESP-IDF build: flasher_args.json lists the images and offsets
j storage flash build/

# Flash several images in one session
j storage flash -t 0x2000:bootloader.bin -t 0x10000:partition-table.bin -t 0x20000:app.bin

# Get chip info
j storage chip-info

# Enter download mode
j storage bootloader

# Erase entire flash
j storage erase

# Hard reset
j storage reset

# Open serial console
j serial console

# Read serial output
j serial pipe
```

`j storage flash` writes every image in one esptool session, prints progress
for each, and then resets the chip.

### Python API

```python
# Get chip information
info = client.storage.get_chip_info()
print(info["chip"])      # e.g. "ESP32-D0WD-V3 (revision v3.1)"
print(info["features"])  # e.g. "Wi-Fi, BT, Dual Core"
print(info["mac"])       # e.g. "5c:01:3b:68:ab:0c"

# Flash firmware
client.storage.flash("/path/to/firmware.bin", target="0x1000")

# Flash an ESP-IDF build, or several images, in one esptool session
client.storage.flash("/path/to/build")
client.storage.flash({"0x2000": "bootloader.bin", "0x10000": "partition-table.bin", "0x20000": "app.bin"})

# Follow progress while flashing
for status in client.storage.flash_stream("/path/to/build"):
    print(status.step_name, status.message, status.progress)

# Enter download mode
client.storage.enter_bootloader()

# Erase flash
client.storage.erase()

# Hard reset
client.storage.hard_reset()

# Serial console via pexpect
console = client.serial.open()
console.sendline("import machine")
console.expect(">>>")
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_esp32.client.Esp32FlasherClient()
    :members: flash, flash_stream, dump, get_chip_info, erase, hard_reset, enter_bootloader
```
