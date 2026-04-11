# ADR-0001: Renode Integration Approach

| Field              | Value                                        |
|--------------------|----------------------------------------------|
| **ADR**            | 0001                                         |
| **Title**          | Renode Integration Approach                  |
| **Author(s)**      | @vtz (Vinicius Zein)                         |
| **Status**         | Accepted                                     |
| **Type**           | Standards Track                              |
| **Created**        | 2026-04-06                                   |
| **Updated**        | 2026-04-11                                   |
| **Discussion**     | [PR #533](https://github.com/jumpstarter-dev/jumpstarter/pull/533) |

---

## Abstract

This ADR documents the architectural decisions behind integrating the
[Renode](https://renode.io/) emulation framework into Jumpstarter as a
new driver package (`jumpstarter-driver-renode`). The driver enables
microcontroller-class virtual targets running bare-metal firmware or
RTOS on Cortex-M and RISC-V MCUs, complementing the existing QEMU
driver which targets Linux-capable SoCs.

## Motivation

Jumpstarter provides a driver-based framework for interacting with
devices under test, both physical hardware and virtual systems. The
existing QEMU driver enables Linux-class virtual targets (aarch64,
x86_64) using full-system emulation with virtio devices and cloud-init
provisioning.

There is growing demand for **microcontroller-class** virtual targets
running bare-metal firmware or RTOS (Zephyr, FreeRTOS, ThreadX) on
Cortex-M and RISC-V MCUs. Renode by Antmicro is an open-source
emulation framework designed specifically for this domain, with
extensive peripheral models for STM32, NXP S32K, Nordic, SiFive, and
other MCU platforms.

### Reference Targets

The initial targets for validation are:

- **STM32F407 Discovery** (Cortex-M4F) -- opensomeip FreeRTOS/ThreadX
  ports, Renode built-in platform
- **NXP S32K388** (Cortex-M7) -- opensomeip Zephyr port, custom
  platform description
- **Nucleo H753ZI** (Cortex-M7) -- openbsw-zephyr, Renode built-in
  `stm32h743.repl`

### Constraints

- The driver must follow Jumpstarter's established composite driver
  pattern (as demonstrated by `jumpstarter-driver-qemu`)
- Users must be able to define new Renode targets through configuration
  alone, without modifying driver code
- The solution should minimize external dependencies and runtime
  requirements
- The UART/console interface must be compatible with Jumpstarter's
  existing `PySerial` and `pexpect` tooling
- The async framework must be `anyio` (the project's standard)

## Design Decisions

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

## Rejected Alternatives

Beyond the alternatives listed in each Design Decision above, the
high-level alternative of **not integrating Renode** and instead
extending the QEMU driver for MCU targets was considered. QEMU's MCU
support (e.g., `qemu-system-arm -M stm32vldiscovery`) is limited in
peripheral modeling and does not match Renode's breadth for embedded
platforms. The QEMU driver remains the right choice for Linux-capable
SoCs while Renode fills the MCU gap.

## Prior Art

- **jumpstarter-driver-qemu** -- The existing Jumpstarter QEMU driver
  established the composite driver pattern, `Popen`-based process
  management, and side-channel control protocol (QMP) that this ADR
  follows.
- **Renode documentation** -- [Renode docs](https://renode.readthedocs.io/)
  for monitor commands, platform descriptions, and UART terminal types.
- **opensomeip** -- [github.com/vtz/opensomeip](https://github.com/vtz/opensomeip)
  provides the reference Renode targets (STM32F407, S32K388) used for
  validation.

## Implementation History

- 2026-04-06: ADR proposed
- 2026-04-09: Initial implementation merged ([PR #533](https://github.com/jumpstarter-dev/jumpstarter/pull/533))
- 2026-04-11: Address review feedback (DEVNULL, try-except cleanup,
  async wait, RenodeMonitorError, multi-word CLI, docstrings)

## References

- [PR #533: Add Renode emulator driver](https://github.com/jumpstarter-dev/jumpstarter/pull/533)
- [Renode project](https://renode.io/)
- [Renode documentation](https://renode.readthedocs.io/)
- [JEP process (PR #423)](https://github.com/jumpstarter-dev/jumpstarter/pull/423)

---

*This ADR is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
