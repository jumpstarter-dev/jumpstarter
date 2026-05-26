# Renode Driver

`jumpstarter-driver-renode` provides a Jumpstarter driver for the
[Renode](https://renode.io/) embedded systems emulation framework. It
enables microcontroller-class virtual targets (Cortex-M, RISC-V MCUs)
running bare-metal firmware or RTOS as Jumpstarter test targets.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-renode
```

Renode must be installed separately and available in `PATH`. See
[Renode installation](https://renode.readthedocs.io/en/latest/introduction/installing.html).

## Configuration

Users define Renode targets entirely through YAML configuration. No
code changes are needed for new targets.

### Configuration Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `platform` | `str` | *(required)* | Path to `.repl` file or Renode built-in platform name |
| `uart` | `str` | `sysbus.uart0` | Peripheral path for the console UART |
| `machine_name` | `str` | `machine-0` | Name of the Renode machine instance |
| `monitor_port` | `int` | `0` (auto) | TCP port for the Renode monitor (0 = auto-assign) |
| `extra_commands` | `list[str]` | `[]` | Additional monitor commands run after platform load |

### Examples

#### STM32F407 Discovery (opensomeip FreeRTOS/ThreadX)

```{literalinclude} ../../../../../packages/jumpstarter-driver-renode/examples/config.yaml
:language: yaml
```

#### NXP S32K388 (opensomeip Zephyr)

```{literalinclude} ../../../../../packages/jumpstarter-driver-renode/examples/config_nxp_s32k388.yaml
:language: yaml
```

#### Nucleo H753ZI (openbsw-zephyr)

```{literalinclude} ../../../../../packages/jumpstarter-driver-renode/examples/config_nucleo_h753zi.yaml
:language: yaml
```

## Usage

### Programmatic (pytest)

```{literalinclude} ../../../../../packages/jumpstarter-driver-renode/examples/usage.py
:language: python
```

### Monitor Commands

Send arbitrary Renode monitor commands via the client:

```{code-block} python
response = renode.monitor_cmd("sysbus GetRegistrationPoints sysbus.usart2")
```

The `monitor` CLI subcommand is also available inside a `jmp shell` session.

## Architecture

The driver follows the composite driver pattern:

- **`Renode`** - root composite driver, manages the simulation lifecycle
- **`RenodePower`** - starts/stops the Renode process and controls the
  simulation via the telnet monitor interface
- **`RenodeFlasher`** - loads firmware (ELF/BIN/HEX) into the simulated MCU
- **`console`** - UART output via PTY terminal, reusing the `PySerial` driver

### Design Decisions

Key decisions:

- **Control interface**: Telnet monitor via `anyio.connect_tcp` (no
  pyrenode3 / .NET dependency)
- **UART exposure**: PTY terminal reusing `PySerial` (consistent with QEMU)
- **Configuration model**: Managed mode with `extra_commands` for
  target-specific customization
- **Firmware loading**: `flash()` stores path, `on()` loads into simulation

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_renode.driver.Renode()
```
