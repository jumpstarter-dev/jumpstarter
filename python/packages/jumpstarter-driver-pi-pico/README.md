# PiPico Driver

`jumpstarter-driver-pi-pico` flashes Raspberry Pi **Pico** (RP2040), **Pico W**, and **Pico 2** (RP2350) by copying a UF2 file onto the **BOOTSEL** USB mass-storage volume.

The driver supports two methods for entering BOOTSEL mode programmatically:

1. **GPIO reset** — wire the Pico's BOOTSEL pad and RUN pin to host GPIO
   lines.
2. **1200-baud serial touch** — uses a USB CDC serial child. Only works when
   the running firmware implements the convention (Pico SDK `pico_stdio_usb`,
   CircuitPython, Arduino).

## Configuration

### Serial-based BOOTSEL entry

```yaml
export:
  storage:
    type: jumpstarter_driver_pi_pico.driver.PiPico
    config: {}
    children:
      serial:
        ref: serial
  serial:
    type: jumpstarter_driver_pyserial.driver.PySerial
    config:
      url: /dev/ttyACM0
      baudrate: 115200
```

### GPIO-based BOOTSEL entry

When the firmware doesn't support the 1200-baud reset, you can wire two host
GPIO pins to the Pico:

| Host GPIO | Pico pin | Notes |
|-----------|----------|-------|
| Pin A | BOOTSEL (TP6 on Pico) | Pull low to select bootloader on reset |
| Pin B | RUN | Pull low to reset the RP2040/RP2350 |

Both GPIO outputs should use **open-drain** drive and **active-low** polarity so
that `on()` pulls the line LOW and `off()` releases to high-impedance (the
Pico's internal pull-ups keep the lines high when released).

```yaml
export:
  storage:
    type: jumpstarter_driver_pi_pico.driver.PiPico
    config: {}
    children:
      serial:
        ref: serial
      bootsel:
        ref: bootsel
      run:
        ref: run
  serial:
    type: jumpstarter_driver_pyserial.driver.PySerial
    config:
      url: /dev/ttyACM0
      baudrate: 115200
  bootsel:
    type: jumpstarter_driver_gpiod.driver.DigitalOutput
    config:
      device: "/dev/gpiochip4"   # RPi5 GPIO chip — adjust for your host
      line: 17                    # GPIO pin wired to BOOTSEL
      drive: open_drain
      active_low: true
      initial_value: inactive
  run:
    type: jumpstarter_driver_gpiod.driver.DigitalOutput
    config:
      device: "/dev/gpiochip4"
      line: 27                    # GPIO pin wired to RUN
      drive: open_drain
      active_low: true
      initial_value: inactive
```

When both GPIO and serial children are present, GPIO reset is preferred.

## Shell commands

- `j storage flash ...` — flash a UF2 file (auto-enters BOOTSEL if needed)
- `j storage bootloader` — request BOOTSEL mode without flashing
- `j serial ...` — USB CDC console (when serial child is configured)

## API

- **`flash(source, target=None)`** — Copies a UF2 from a Jumpstarter resource to the BOOTSEL volume. `target` is the destination filename (default `Firmware.uf2`).
- **`enter_bootloader()`** — Enters BOOTSEL mode via GPIO reset or 1200-baud serial touch.
- **`bootloader_info()`** — Parses `INFO_UF2.TXT` from the mounted volume.
- **`dump`** — Not supported over UF2 mass storage.
