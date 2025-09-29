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
  serial:
      type: jumpstarter_driver_pyserial.driver.PySerial
      config:
        url: "/dev/ttyUSB0"
        baudrate: 115200
```

### Config parameters

| Parameter    | Description                                                           | Type | Required | Default     |
| ------------ | --------------------------------------------------------------------- | ---- | -------- | ----------- |
| port         | The serial port to connect to the ESP32                              | str  | yes      |             |
| baudrate     | The baudrate for serial communication                                | int  | no       | 115200      |
| chip         | The ESP32 chip type (esp32, esp32s2, esp32s3, esp32c3, etc.)        | str  | no       | esp32       |
| reset_pin    | GPIO pin number for hardware reset (if connected)                    | int  | no       | null        |
| boot_pin     | GPIO pin number for boot mode control (if connected)                 | int  | no       | null        |

## API Reference

```{autoclass} jumpstarter_driver_esp32.driver.ESP32
:members:
```

# Examples

```shell
$ j esp32 flash ESP32_GENERIC-20250911-v1.26.1.bin -a 0x1000
```
