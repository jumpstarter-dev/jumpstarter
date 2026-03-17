# Quickstart: Verifying the Fix

## Prerequisites

- Python 3.11+
- UV package manager
- The jumpstarter monorepo checked out on the `007-fix-driver-list` branch

## Steps to Verify

### 1. Install the packages in development mode

From the repository root:

```bash
make install
```

Or install specific driver packages:

```bash
uv pip install -e python/packages/jumpstarter-driver-ble
uv pip install -e python/packages/jumpstarter-driver-pyserial
# ... (repeat for each fixed package)
```

### 2. Run the driver list command

```bash
jmp driver list
```

### 3. Expected output

The output should include ALL installed drivers, including previously missing ones
such as:

- BleWriteNotifyStream
- PySerial
- HttpServer
- HttpPower
- ISCSI
- ProbeRs
- QemuFlasher, QemuPower, Qemu
- RideSXDriver, RideSXPowerDriver
- SNMPServer
- SSHWrapper
- Tftp
- TMT
- UbootConsole
- UStreamer
- TIJ784S4Flasher, RCarS4Flasher

### 4. Verify with Python directly

```python
from importlib.metadata import entry_points

drivers = entry_points(group="jumpstarter.drivers")
for ep in sorted(drivers, key=lambda e: e.name):
    print(f"{ep.name} = {ep.value}")
```

### 5. Run tests

```bash
make pkg-test-jumpstarter_cli_driver
```

## What was wrong

16 driver packages were missing the `[project.entry-points."jumpstarter.drivers"]`
section in their `pyproject.toml`. Without this metadata, the Python packaging system
had no way to advertise these drivers to `importlib.metadata.entry_points()`, causing
`jmp driver list` to show an incomplete list.

## What the fix does

Adds the `[project.entry-points."jumpstarter.drivers"]` section to each of the 15
affected driver packages (excluding `jumpstarter-driver-uds` which is an abstract
interface, not a concrete driver).
