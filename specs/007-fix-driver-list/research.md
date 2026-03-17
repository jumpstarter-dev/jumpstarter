# Research: Fix Driver List (007-fix-driver-list)

## Problem

The `jmp driver list` command uses `entry_points(group="jumpstarter.drivers")` from
`importlib.metadata` to discover installed drivers. Driver packages that lack the
`[project.entry-points."jumpstarter.drivers"]` section in their `pyproject.toml` are
invisible to this command.

## Discovery Mechanism

File: `python/packages/jumpstarter-cli-driver/jumpstarter_cli_driver/driver.py`

The command calls `entry_points(group="jumpstarter.drivers")` which queries the Python
packaging metadata. Each driver package must declare its driver classes under the
`[project.entry-points."jumpstarter.drivers"]` table in `pyproject.toml`.

## Audit of All Driver Packages

### Packages WITH entry-points (18 packages -- no action needed)

| Package | Entry-Point Names |
|---------|-------------------|
| jumpstarter-driver-can | Can, IsoTpPython, IsoTpSocket |
| jumpstarter-driver-composite | Composite, Proxy |
| jumpstarter-driver-corellium | Corellium |
| jumpstarter-driver-doip | DoIP |
| jumpstarter-driver-dutlink | Dutlink, DutlinkSerial, DutlinkStorageMux, DutlinkPower |
| jumpstarter-driver-energenie | EnerGenie |
| jumpstarter-driver-esp32 | Esp32Flasher |
| jumpstarter-driver-gpiod | DigitalInput, DigitalOutput, PowerSwitch |
| jumpstarter-driver-network | TcpNetwork, UdpNetwork, UnixNetwork, EchoNetwork |
| jumpstarter-driver-power | MockPower |
| jumpstarter-driver-sdwire | SDWire |
| jumpstarter-driver-shell | Shell |
| jumpstarter-driver-ssh-mitm | ssh_mitm (module-level) |
| jumpstarter-driver-tasmota | TasmotaPower |
| jumpstarter-driver-uds-can | UdsCan |
| jumpstarter-driver-uds-doip | UdsDoip |
| jumpstarter-driver-vnc | vnc -> Vnc |
| jumpstarter-driver-yepkit | Ykush |

### Packages MISSING entry-points (16 packages -- fix required)

| Package | Driver Class(es) in driver.py | Proposed Entry-Point Name(s) |
|---------|-------------------------------|------------------------------|
| jumpstarter-driver-ble | BleWriteNotifyStream(Driver) | BleWriteNotifyStream |
| jumpstarter-driver-flashers | TIJ784S4Flasher(BaseFlasher), RCarS4Flasher(BaseFlasher) | TIJ784S4Flasher, RCarS4Flasher |
| jumpstarter-driver-http | HttpServer(Driver) | HttpServer |
| jumpstarter-driver-http-power | HttpPower(PowerInterface, Driver) | HttpPower |
| jumpstarter-driver-iscsi | ISCSI(Driver) | ISCSI |
| jumpstarter-driver-probe-rs | ProbeRs(Driver) | ProbeRs |
| jumpstarter-driver-pyserial | PySerial(Driver) | PySerial |
| jumpstarter-driver-qemu | QemuFlasher(FlasherInterface, Driver), QemuPower(PowerInterface, Driver), Qemu(Driver) | QemuFlasher, QemuPower, Qemu |
| jumpstarter-driver-ridesx | RideSXDriver(Driver), RideSXPowerDriver(Driver) | RideSXDriver, RideSXPowerDriver |
| jumpstarter-driver-snmp | SNMPServer(Driver) | SNMPServer |
| jumpstarter-driver-ssh | SSHWrapper(Driver) | SSHWrapper |
| jumpstarter-driver-tftp | Tftp(Driver) | Tftp |
| jumpstarter-driver-tmt | TMT(Driver) | TMT |
| jumpstarter-driver-uboot | UbootConsole(Driver) | UbootConsole |
| jumpstarter-driver-ustreamer | UStreamer(Driver) | UStreamer |

### Packages intentionally WITHOUT jumpstarter.drivers entry-points

| Package | Reason |
|---------|--------|
| jumpstarter-driver-opendal | Uses `jumpstarter.adapters` group instead (not a driver) |
| jumpstarter-driver-uds | Interface/base class only (`UdsInterface`); concrete drivers are in uds-can and uds-doip |

## Existing Entry-Point Pattern

The standard format is:

```toml
[project.entry-points."jumpstarter.drivers"]
ClassName = "module_name.driver:ClassName"
```

Where the entry-point name matches the class name, and the value is `module:Class`.

## BaseFlasher Note

In `jumpstarter-driver-flashers`, `BaseFlasher(Driver)` is abstract. Only
`TIJ784S4Flasher` and `RCarS4Flasher` should be registered.
