# STM32F407 Rust blink (Renode)

This folder holds a **Jumpstarter exporter** plus a **prebuilt firmware** so you can run an STM32F407 blink demo inside **Renode** and drive it from the shell.

## Files

| File | Role |
|------|------|
| `exporter-stm32f4.yaml` | Exporter config: Renode platform (`stm32f4_discovery-kit.repl`), UART on `sysbus.usart2`, and the export name `ecu`. |
| `.cargo/config.toml` | Rust embedded target (`thumbv7em-none-eabihf`) and link script flags when you build firmware for this board. |
| `Cargo.toml`, `src/main.rs` | Firmware: toggles the Discovery user LED on **PD12**, prints **`ON` / `OFF`** on **USART2** (PA2 @ 115200 8N1), matching Renode's `UserLED` and `usart2`. |
| `memory.x`, `build.rs` | Linker memory map for the STM32F407 and `cortex-m-rt` (`link.x`). |
| `rust-toolchain.toml` | Pins **stable** and the **`thumbv7em-none-eabihf`** target via rustup. |
| `Makefile` | The `firmware` target builds, strips, and installs `stm32f407-rust-blink.elf`. |
| `stm32f407-rust-blink.elf` | Stripped copy of the release ELF for flashing; the canonical build artifact is under `target/.../release/`. |
| `target/` | Rust build output (if you build here). |

## Build the firmware

Install the [rustup](https://rustup.rs/) toolchain and the embedded target (`rust-toolchain.toml` requests it). The `firmware` target also runs `cargo strip`, which cargo does not include; [cargo-binutils](https://github.com/rust-embedded/cargo-binutils) provides it (`cargo install cargo-binutils` and `rustup component add llvm-tools`). From the repository root:

```bash
PATH="$HOME/.cargo/bin:$PATH" make -C python/packages/jumpstarter-driver-renode/examples/stm32f407-rust-blink firmware
```

The `firmware` target builds the release firmware, strips it, and installs it as `stm32f407-rust-blink.elf` - the same (stripped) artifact that is committed in this folder. `cargo strip` strips `target/thumbv7em-none-eabihf/release/stm32f407-rust-blink` in place; the `build` target leaves an unstripped build there.

## Workflow

1. **Start an exporter shell** wired to this exporter (from this directory):

   ```bash
   jmp shell --exporter-config exporter-stm32f4.yaml
   ```

2. Inside that shell, `j` is the client for the exported **composite device**. The Renode stack is grouped under **`j ecu`** (not a top-level `j serial`).

3. **Flash** the firmware:

   ```bash
   j ecu flasher flash stm32f407-rust-blink.elf
   ```

4. **Turn simulation on** and **open the serial console** (USART2 in this config):

   ```bash
   j ecu power on && j ecu console console
   ```

   To reset the simulation and attach the console again:

   ```bash
   j ecu power cycle && j ecu console console
   ```

5. **Leave the console**: press **Ctrl+B three times** (as printed when the console starts).

## `j ecu` commands (reference)

Under `j ecu` you typically use:

- **`flasher`** - load an ELF into the target.
- **`power`** - `on`, `off`, `cycle`, etc. (starts/controls the Renode simulation for this exporter).
- **`console`** - serial client (e.g. `console`) to the UART defined in the YAML.
- **`monitor`** - send commands to the Renode monitor. This needs `allow_raw_monitor: true` under `config:` in the exporter YAML, which `exporter-stm32f4.yaml` leaves off because it lets the client send any command to the Renode monitor.

Run `j ecu --help` for the full list.

## Requirements

- **Renode** installed and on `PATH` (the driver spawns it for power/simulation).
- Jumpstarter CLI (`jmp`) and the **Renode driver** available to the exporter.
