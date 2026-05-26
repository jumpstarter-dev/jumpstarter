# ST-LINK MSD Driver

`jumpstarter-driver-stlink-msd` flashes STM32 **Nucleo** and **Discovery** boards by copying
firmware to the **ST-LINK USB mass storage volume**.

This is an alternative to probe-rs that avoids known [connect-under-reset issues
with ST-Link V3](https://github.com/probe-rs/probe-rs/issues/3516). The ST-LINK's
built-in mass storage interface handles all the flash programming.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-stlink-msd
```

## Configuration

```{literalinclude} ../../../../../packages/jumpstarter-driver-stlink-msd/examples/config.yaml
:language: yaml
```

| Parameter     | Description                                                      | Type           | Required | Default      |
|---------------|------------------------------------------------------------------|----------------|----------|--------------|
| volume_name   | Name of the mounted ST-LINK volume (e.g. `NOD_H755ZI`)          | str \| None    | no       | auto-detect  |

### Supported Formats

| Format | Handling |
|--------|----------|
| `.bin` | Copied directly to the ST-LINK volume |
| `.hex` | Copied directly to the ST-LINK volume |

ELF files must be converted externally before flashing:

```shell
arm-none-eabi-objcopy -O binary zephyr.elf zephyr.bin
```

## Usage

```shell
j flasher flash firmware.bin       # flash a raw binary
j flasher flash firmware.hex       # flash an Intel HEX file
j flasher info                     # show ST-LINK volume details
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_stlink_msd.driver.StlinkMsdFlasher()
```
