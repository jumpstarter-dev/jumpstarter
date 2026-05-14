# ST-LINK Mass Storage Flasher

`jumpstarter-driver-stlink-msd` flashes STM32 **Nucleo** and **Discovery** boards by copying
firmware to the **ST-LINK USB mass storage volume**.

This is an alternative to probe-rs that avoids known [connect-under-reset issues
with ST-Link V3](https://github.com/probe-rs/probe-rs/issues/3516). The ST-LINK's
built-in mass storage interface handles all the flash programming.

## Supported Formats

| Format | Handling |
|--------|----------|
| `.bin` | Copied directly to the ST-LINK volume |
| `.hex` | Copied directly to the ST-LINK volume |

ELF files must be converted externally before flashing:

```shell
arm-none-eabi-objcopy -O binary zephyr.elf zephyr.bin
```

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-stlink-msd
```

## Configuration

```yaml
export:
  flasher:
    type: jumpstarter_driver_stlink_msd.driver.StlinkMsdFlasher
    config:
      # volume_name: "NOD_H755ZI"   # optional: auto-detected if only one ST-LINK is connected
```

| Parameter     | Description                                                      | Type           | Required | Default      |
|---------------|------------------------------------------------------------------|----------------|----------|--------------|
| volume_name   | Name of the mounted ST-LINK volume (e.g. `NOD_H755ZI`)          | str \| None    | no       | auto-detect  |

## Shell Commands

```shell
j flasher flash firmware.bin       # flash a raw binary
j flasher flash firmware.hex       # flash an Intel HEX file
j flasher info                     # show ST-LINK volume details
```

## API

- **`flash(source, target=None)`** — Flash firmware to the board. Accepts `.bin` or `.hex` files.
- **`info()`** — Read `DETAILS.TXT` from the ST-LINK volume and return board metadata.
