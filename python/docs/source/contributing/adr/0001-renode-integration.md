# ADR-0001: Renode Integration Approach

- **Status**: Accepted
- **Date**: 2026-04-06
- **Authors**: Vinicius Zein

## Context

Jumpstarter provides a driver-based framework for interacting with
devices under test, both physical hardware and virtual systems. The
existing QEMU driver enables Linux-class virtual targets (aarch64,
x86_64) using full-system emulation with virtio devices and cloud-init
provisioning.

There is growing demand for **microcontroller-class** virtual targets
running bare-metal firmware or RTOS (Zephyr, FreeRTOS, ThreadX) on
Cortex-M and RISC-V MCUs. [Renode](https://renode.io/) by Antmicro is
an open-source emulation framework designed specifically for this
domain, with extensive peripheral models for STM32, NXP S32K, Nordic,
SiFive, and other MCU platforms.

The initial reference targets for validation are:

- **STM32F407 Discovery** (Cortex-M4F) -- opensomeip FreeRTOS/ThreadX
  ports, Renode built-in platform
- **NXP S32K388** (Cortex-M7) -- opensomeip Zephyr port, custom
  platform description
- **Nucleo H753ZI** (Cortex-M7) -- openbsw-zephyr, Renode built-in
  `stm32h743.repl`

### Forces

- The driver must follow Jumpstarter's established composite driver
  pattern (as demonstrated by `jumpstarter-driver-qemu`)
- Users must be able to define new Renode targets through configuration
  alone, without modifying driver code
- The solution should minimize external dependencies and runtime
  requirements
- The UART/console interface must be compatible with Jumpstarter's
  existing `PySerial` and `pexpect` tooling
- The async framework must be `anyio` (the project's standard)

## Decisions

### DD-1: Control Interface -- Telnet Monitor

**Alternatives considered:**

1. **Telnet monitor** -- Renode's built-in TCP monitor interface.
   Simple socket connection, send text commands, read responses.
   Lightweight, no extra runtime needed.
2. **pyrenode3** -- Python.NET bridge to Renode's C# internals. More
   powerful but requires .NET runtime or Mono, heavy dependency, less
   stable API surface.

**Decision:** Telnet monitor.

**Rationale:** It is the lowest-common-denominator interface that works
with any Renode installation. It mirrors the QEMU driver's pattern
where `Popen` starts the emulator process and a side-channel protocol
(QMP for QEMU, telnet monitor for Renode) provides programmatic
control. The monitor client uses `anyio.connect_tcp` with
`anyio.fail_after` for timeouts, consistent with `TcpNetwork` and
`grpc.py` in the project. No `telnetlib`, `telnetlib3`, or
`asynctelnet` is introduced since these are not used anywhere in the
project.

### DD-2: UART Exposure -- PTY Terminal

**Alternatives considered:**

1. **PTY** (`emulation CreateUartPtyTerminal`) -- Creates a
   pseudo-terminal file on the host. Reuses the existing `PySerial`
   child driver exactly as QEMU does. Linux/macOS only.
2. **Socket** (`emulation CreateServerSocketTerminal`) -- Exposes UART
   as a TCP socket. Cross-platform. Maps to `TcpNetwork` driver. Has
   telnet IAC negotiation bytes to handle.

**Decision:** PTY as the primary interface.

**Rationale:** Consistency with the QEMU driver, which uses `-serial
pty` and wires a `PySerial` child driver to the discovered PTY path.
This reuses the same serial/pexpect/console tooling without any
adaptation. Socket terminal support can be added later as a fallback
for platforms without PTY support.

### DD-3: Configuration Model -- Managed Mode

**Alternatives considered:**

1. **Managed mode** -- The driver constructs all Renode monitor
   commands from YAML config parameters (`platform`, `uart`, firmware
   path). The driver handles platform loading, UART wiring, and
   firmware loading programmatically.
2. **Script mode** -- User provides a complete `.resc` script. The
   driver runs it but still manages UART terminal setup.

**Decision:** Managed mode as primary, with an `extra_commands` list
for target-specific customization.

**Rationale:** Managed mode gives the driver full control over the UART
terminal setup (which must use PTY for Jumpstarter integration, not the
`CreateFileBackend` or `showAnalyzer` used in typical `.resc` scripts).
The `extra_commands` list covers target-specific needs like register
pokes (e.g., `sysbus WriteDoubleWord 0x40090030 0x0301` for S32K388
PL011 UART enablement) and Ethernet switch setup. The opensomeip `.resc`
files are CI-oriented and their setup maps directly to managed-mode
config parameters.

### DD-4: Firmware Loading -- Deferred to Flash

**Alternatives considered:**

1. `flash()` stores the firmware path, `on()` loads it into the
   simulation and starts
2. `on()` starts the simulation, `flash()` loads firmware and resets

**Decision:** Option 1 -- `flash()` stores the path, `on()` loads and
starts.

**Rationale:** This matches the QEMU driver's semantic where you flash
a disk image first, then power on. It also allows re-flashing between
power cycles without restarting the Renode process. The `RenodeFlasher`
additionally supports hot-loading: if the simulation is already running,
`flash()` sends the `sysbus LoadELF` command and resets the machine.

## Consequences

### Positive

- Single `jumpstarter-driver-renode` package supports any Renode target
  through YAML configuration alone
- No .NET runtime or Mono dependency required
- Consistent user experience with the QEMU driver (same composite
  pattern, same console/pexpect workflow)
- `extra_commands` provides an escape hatch for target-specific
  customization without code changes

### Negative

- PTY-only UART exposure limits to Linux/macOS (acceptable since Renode
  itself primarily targets these platforms)
- The telnet monitor protocol is text-based and less structured than
  QMP's JSON -- error detection requires string matching
- Full `.resc` script support is deferred; users with complex Renode
  setups must express their configuration as managed-mode parameters
  plus `extra_commands`

### Risks

- Renode's monitor protocol has no formal specification; prompt
  detection and error handling rely on observed behavior
- Renode's PTY terminal support on macOS may have edge cases not
  covered in testing
