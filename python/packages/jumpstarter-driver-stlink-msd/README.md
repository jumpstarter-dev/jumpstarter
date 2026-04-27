# ST-LINK Mass Storage Flasher

`jumpstarter-driver-stlink-msd` flashes STM32 **Nucleo** and **Discovery** boards by copying
firmware to the **ST-LINK USB mass storage volume**.

This is an alternative to probe-rs that avoids known [connect-under-reset issues
with ST-Link V3](https://github.com/probe-rs/probe-rs/issues/3516). The ST-LINK's
built-in mass storage interface handles all the flash programming.

## Supported Formats

| Format | Handling |
|--------|----------|
| `.elf` | Auto-converted to `.bin` via `objcopy`, then copied to the volume |
| `.bin` | Copied directly to the ST-LINK volume |
| `.hex` | Copied directly to the ST-LINK volume |

Using `.elf` allows the same build artifact for both virtual (Renode) and physical targets.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-stlink-msd
```

For `.elf` support, ensure one of these is on your `PATH`:

- `arm-none-eabi-objcopy` (ARM GCC toolchain)
- `llvm-objcopy` (LLVM/Clang)
- `arm-zephyr-eabi-objcopy` (Zephyr SDK)

## Configuration

```yaml
export:
  flasher:
    type: jumpstarter_driver_stlink_msd.driver.StlinkMsdFlasher
    config:
      # volume_name: "NOD_H755ZI"   # optional: auto-detected if only one ST-LINK is connected
      # objcopy_path: "/path/to/objcopy"  # optional: auto-detected from PATH
```

| Parameter     | Description                                                      | Type           | Required | Default      |
|---------------|------------------------------------------------------------------|----------------|----------|--------------|
| volume_name   | Name of the mounted ST-LINK volume (e.g. `NOD_H755ZI`)          | str \| None    | no       | auto-detect  |
| objcopy_path  | Path to objcopy binary for ELF-to-BIN conversion                 | str \| None    | no       | auto-detect  |

## Shell Commands

```shell
j flasher flash firmware.elf       # flash an ELF (auto-converts to .bin)
j flasher flash firmware.bin       # flash a raw binary
j flasher info                     # show ST-LINK volume details
```

## API

- **`flash(source, target=None)`** — Flash firmware to the board. Accepts `.elf`, `.bin`, or `.hex`.
  ELF files are automatically converted to `.bin` using `objcopy`.
- **`info()`** — Read `DETAILS.TXT` from the ST-LINK volume and return board metadata.
- **`dump()`** — Not supported (ST-LINK mass storage is write-only).
