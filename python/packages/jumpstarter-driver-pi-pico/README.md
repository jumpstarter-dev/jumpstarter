# Pi Pico Driver

`jumpstarter-driver-pi-pico` flashes Raspberry Pi **Pico** (RP2040), **Pico W**, and **Pico 2** (RP2350) by copying a UF2 file onto the **BOOTSEL** USB mass-storage volume.

The driver supports two methods for entering BOOTSEL mode programmatically:

1. **GPIO reset** - wire the Pico's BOOTSEL pad and RUN pin to host GPIO
   lines.
2. **1200-baud serial touch** - uses a USB CDC serial child. Only works when
   the running firmware implements the convention (Pico SDK `pico_stdio_usb`,
   CircuitPython, Arduino).

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-pi-pico
```

## Configuration

### Serial-based BOOTSEL entry

```{literalinclude} ../../../../../packages/jumpstarter-driver-pi-pico/examples/config.yaml
:language: yaml
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

```{literalinclude} ../../../../../packages/jumpstarter-driver-pi-pico/examples/config_gpio_based_bootsel_entry.yaml
:language: yaml
```

When both GPIO and serial children are present, GPIO reset is preferred.

## Usage

- `j storage flash ...` - flash a UF2 file (auto-enters BOOTSEL if needed)
- `j storage bootloader` - request BOOTSEL mode without flashing
- `j serial ...` - USB CDC console (when serial child is configured)

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_pi_pico.driver.PiPicoFlasher()
```
