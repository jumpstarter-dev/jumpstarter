# JEP-0010: Renode Integration for Microcontroller Targets

| Field              | Value                                        |
|--------------------|----------------------------------------------|
| **JEP**            | 0010                                         |
| **Title**          | Renode Integration for Microcontroller Targets |
| **Author(s)**      | @vtz (Vinicius Zein)                         |
| **Status**         | Implemented                                  |
| **Type**           | Standards Track                              |
| **Created**        | 2026-04-06                                   |
| **Updated**        | 2026-04-15                                   |
| **Discussion**     | [PR #533](https://github.com/jumpstarter-dev/jumpstarter/pull/533) |

---

## Abstract

This JEP proposes integrating the [Renode](https://renode.io/) emulation
framework into Jumpstarter as a new driver package
(`jumpstarter-driver-renode`). The driver enables microcontroller-class
virtual targets running bare-metal firmware or RTOS on Cortex-M and
RISC-V MCUs, complementing the existing QEMU driver which targets
Linux-capable SoCs.

## Motivation

Jumpstarter provides a driver-based framework for interacting with
devices under test, both physical hardware and virtual systems. The
existing QEMU driver enables Linux-class virtual targets (aarch64,
x86_64) using full-system emulation with virtio devices and cloud-init
provisioning.

There is growing demand for **microcontroller-class** virtual targets
running bare-metal firmware or RTOS (Zephyr, FreeRTOS, ThreadX) on
Cortex-M and RISC-V MCUs. QEMU's MCU support is limited in peripheral
modeling and does not match Renode's breadth for embedded platforms.
Renode by Antmicro is an open-source emulation framework designed
specifically for this domain, with extensive peripheral models for
STM32, NXP S32K, Nordic, SiFive, and other MCU platforms.

### User Stories

- **As a** firmware CI pipeline author, **I want to** run my Zephyr
  firmware against a virtual STM32 target in Jumpstarter, **so that**
  I can validate UART output and basic functionality without physical
  hardware.
- **As a** SOME/IP stack developer, **I want to** configure virtual
  NXP S32K388 and STM32F407 targets through exporter YAML, **so that**
  I can test multi-platform firmware builds without modifying driver
  code.
- **As a** Jumpstarter lab operator, **I want to** mix physical boards
  and Renode virtual targets in the same test environment, **so that**
  my team can scale testing beyond available hardware.

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

## Proposal

The `jumpstarter-driver-renode` package provides a composite driver
(`Renode`) that manages a Renode simulation instance with three child
drivers:

- **`RenodePower`** — controls the Renode process lifecycle (on/off)
- **`RenodeFlasher`** — handles firmware loading (`sysbus LoadELF` /
  `sysbus LoadBinary`)
- **`PySerial` (console)** — serial access over a PTY terminal

Users configure targets entirely through exporter YAML:

```yaml
export:
  Renode:
    platform: "platforms/boards/stm32f4_discovery-kit.repl"
    uart: "sysbus.usart2"
    machine_name: "stm32f4"
    extra_commands:
      - "sysbus WriteDoubleWord 0x40090030 0x0301"
    allow_raw_monitor: false
```

The driver starts Renode with `--disable-xwt --plain --port <N>`,
connects to the telnet monitor via `anyio.connect_tcp`, and
programmatically sets up the platform, UART terminal, and firmware.

A `RenodeMonitor` async client handles the telnet protocol:
- Retry-based connection with `fail_after(timeout)`
- Prompt detection using registered machine names
- Per-line error marker detection
- Newline injection prevention
- Configurable command timeout

### API / Protocol Changes

No gRPC protocol changes. The driver exposes standard Jumpstarter
interfaces (`PowerInterface`, `FlasherInterface`) plus:

- `get_platform()`, `get_uart()`, `get_machine_name()` — read-only
  config accessors
- `monitor_cmd(command)` — raw monitor access, gated behind
  `allow_raw_monitor: true` (default: `false`)

### Hardware Considerations

No physical hardware required. Renode is a pure software emulator.
The driver uses PTY terminals for UART, which requires a POSIX
system (Linux or macOS). The Renode process is managed via
`subprocess.Popen`.

## Design Decisions

### DD-1: Control Interface — Telnet Monitor

**Alternatives considered:**

1. **Telnet monitor** — Renode's built-in TCP monitor interface.
   Simple socket connection, send text commands, read responses.
   Lightweight, no extra runtime needed.
2. **pyrenode3** — Python.NET bridge to Renode's C# internals. More
   powerful but requires .NET runtime or Mono, heavy dependency, less
   stable API surface.

**Decision:** Telnet monitor.

**Rationale:** It is the lowest-common-denominator interface that works
with any Renode installation. It mirrors the QEMU driver's pattern
where `Popen` starts the emulator process and a side-channel protocol
(QMP for QEMU, telnet monitor for Renode) provides programmatic
control. The monitor client uses `anyio.connect_tcp` with
`anyio.fail_after` for timeouts, consistent with `TcpNetwork` and
`grpc.py` in the project.

### DD-2: UART Exposure — PTY Terminal

**Alternatives considered:**

1. **PTY** (`emulation CreateUartPtyTerminal`) — Creates a
   pseudo-terminal file on the host. Reuses the existing `PySerial`
   child driver exactly as QEMU does. Linux/macOS only.
2. **Socket** (`emulation CreateServerSocketTerminal`) — Exposes UART
   as a TCP socket. Cross-platform. Maps to `TcpNetwork` driver. Has
   telnet IAC negotiation bytes to handle.

**Decision:** PTY as the primary interface.

**Rationale:** Consistency with the QEMU driver, which uses `-serial
pty` and wires a `PySerial` child driver to the discovered PTY path.
This reuses the same serial/pexpect/console tooling without any
adaptation. Socket terminal support can be added later as a fallback
for platforms without PTY support.

### DD-3: Configuration Model — Managed Mode

**Alternatives considered:**

1. **Managed mode** — The driver constructs all Renode monitor
   commands from YAML config parameters (`platform`, `uart`, firmware
   path). The driver handles platform loading, UART wiring, and
   firmware loading programmatically.
2. **Script mode** — User provides a complete `.resc` script. The
   driver runs it but still manages UART terminal setup.

**Decision:** Managed mode as primary, with an `extra_commands` list
for target-specific customization.

**Rationale:** Managed mode gives the driver full control over the UART
terminal setup (which must use PTY for Jumpstarter integration, not the
`CreateFileBackend` or `showAnalyzer` used in typical `.resc` scripts).
The `extra_commands` list covers target-specific needs like register
pokes (e.g., `sysbus WriteDoubleWord 0x40090030 0x0301` for S32K388
PL011 UART enablement) and Ethernet switch setup.

### DD-4: Firmware Loading — Deferred to Flash

**Alternatives considered:**

1. `flash()` stores the firmware path, `on()` loads it into the
   simulation and starts
2. `on()` starts the simulation, `flash()` loads firmware and resets

**Decision:** Option 1 — `flash()` stores the path, `on()` loads and
starts.

**Rationale:** This matches the QEMU driver's semantic where you flash
a disk image first, then power on. It also allows re-flashing between
power cycles without restarting the Renode process. The `RenodeFlasher`
additionally supports hot-loading: if the simulation is already running,
`flash()` sends the load command and resets the machine.

### DD-5: Security — Restricted Monitor Access

**Alternatives considered:**

1. **Open access** — Expose `monitor_cmd` to all authenticated clients
2. **Opt-in access** — Gate behind `allow_raw_monitor` config flag

**Decision:** Opt-in with `allow_raw_monitor: false` by default.

**Rationale:** The Renode monitor supports commands that interact with
the host filesystem (`logFile`, `include`, `CreateFileTerminal`). In a
shared lab environment, unrestricted monitor access from any
authenticated client poses a risk. The `load_command` parameter in
`flash()` is separately validated against an allowlist of known Renode
load commands. Newline characters are rejected in all monitor commands
to prevent command injection.

## Design Details

### Component Architecture

```
Renode (composite driver)
├── RenodePower      → manages Popen lifecycle + RenodeMonitor
├── RenodeFlasher    → writes firmware, sends LoadELF/LoadBinary
└── PySerial         → console over PTY terminal
```

### Monitor Protocol

The `RenodeMonitor` client connects to Renode's telnet port and
communicates via line-oriented text:

1. **Connection**: retry loop with `fail_after(timeout)`, closing
   leaked streams on retry
2. **Prompt detection**: matches `(monitor)` or registered machine
   names only — no false positives from output like `(enabled)`
3. **Error detection**: per-line check against markers (`Could not
   find`, `Error`, `Invalid`, `Failed`, `Unknown`)
4. **Timeout**: `execute()` wraps reads in `fail_after(30)` to prevent
   indefinite blocking
5. **Injection prevention**: newline characters rejected in commands;
   `load_command` validated against allowlist

### Firmware Auto-Detection

When `load_command` is not specified, the driver reads the first 4
bytes of the firmware file. If they match the ELF magic (`\x7fELF`),
`sysbus LoadELF` is used; otherwise `sysbus LoadBinary`.

## Test Plan

### Unit Tests

- `TestRenodeMonitor` — connection retry, command execution, error
  detection (per-line), disconnect, newline rejection, stream cleanup
  on retry, prompt matching against expected prompts only
- `TestRenodePower` — command sequence verification, extra commands
  ordering, firmware-less boot, idempotent on/off, process termination
  and cleanup
- `TestRenodeFlasher` — firmware path storage, hot-load with reset,
  custom load command, invalid load command rejection, ELF magic
  detection, dump not-implemented
- `TestRenodeConfig` — default values, children wiring, custom config,
  PTY path construction, lifecycle

### Integration Tests

- `TestRenodeClient` — round-trip properties via `serve()`, children
  accessibility, `monitor_cmd` disabled by default, `monitor_cmd` not
  running error, CLI rendering

### E2E Tests

- `test_driver_renode_e2e` — full power on/off cycle with real Renode
  process, skipped when Renode is not installed

### CI

Renode is installed in the `python-tests.yaml` workflow:
- Linux: `.deb` package from builds.renode.io
- macOS: `brew install renode`

## Backward Compatibility

This JEP introduces a new driver package with no changes to existing
packages. There are no breaking changes. The `jumpstarter-all`
meta-package includes the new driver as an optional dependency.

## Consequences

### Positive

- Single `jumpstarter-driver-renode` package supports any Renode target
  through YAML configuration alone
- No .NET runtime or Mono dependency required
- Consistent user experience with the QEMU driver (same composite
  pattern, same console/pexpect workflow)
- `extra_commands` provides an escape hatch for target-specific
  customization without code changes
- Security-by-default with `allow_raw_monitor: false`

### Negative

- PTY-only UART exposure limits to Linux/macOS (acceptable since Renode
  itself primarily targets these platforms)
- The telnet monitor protocol is text-based and less structured than
  QMP's JSON — error detection requires string matching
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

- **jumpstarter-driver-qemu** — The existing Jumpstarter QEMU driver
  established the composite driver pattern, `Popen`-based process
  management, and side-channel control protocol (QMP) that this JEP
  follows.
- **Renode documentation** — [Renode docs](https://renode.readthedocs.io/)
  for monitor commands, platform descriptions, and UART terminal types.
- **opensomeip** — [github.com/vtz/opensomeip](https://github.com/vtz/opensomeip)
  provides the reference Renode targets (STM32F407, S32K388) used for
  validation.

## Future Possibilities

- **Socket terminal fallback** for Windows/cross-platform UART access
- **`.resc` script mode** for users with complex existing Renode setups
- **Multi-machine simulation** for testing inter-MCU communication
- **Renode metrics integration** for performance profiling

## Implementation History

- 2026-04-06 JEP proposed
- 2026-04-06: Initial implementation proposed
  ([PR #533](https://github.com/jumpstarter-dev/jumpstarter/pull/533))
- 2026-04-11: Address review feedback (DEVNULL, try-except cleanup,
  async wait, RenodeMonitorError, multi-word CLI, docstrings)
- 2026-04-15: Security hardening (load_command allowlist,
  allow_raw_monitor, newline rejection, per-line error detection,
  prompt matching, execute timeout, stream leak fix)

## References

- [PR #533: Add Renode emulator driver](https://github.com/jumpstarter-dev/jumpstarter/pull/533)
- [Renode project](https://renode.io/)
- [Renode documentation](https://renode.readthedocs.io/)
- [JEP process (PR #423)](https://github.com/jumpstarter-dev/jumpstarter/pull/423)

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
