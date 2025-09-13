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

```{testcode}
from jumpstarter.common.utils import env
from jumpstarter.config.client import ClientConfigV1Alpha1

def main():
    try:
        # Try to use existing JUMPSTARTER_HOST environment variable
        with env() as client:
            run_esp32_example(client)
    except RuntimeError:
        # Fallback to creating a lease (requires jumpstarter configuration)
        config = ClientConfigV1Alpha1.load("default")
        with config.lease(selector="driver=esp32") as lease:
            with lease.connect() as client:
                run_esp32_example(client)

def run_esp32_example(client):
    esp32 = client.esp32

    # Get chip information
    info = esp32.chip_info()
    print(f"Connected to {info['chip_revision']}")
    print(f"MAC Address: {info['mac_address']}")

    # Flash firmware
    result = esp32.flash_firmware_file("firmware.bin", address=0x10000)
    print(result)

    # Reset device to run new firmware
    esp32.reset()

if __name__ == "__main__":
    main()
```
