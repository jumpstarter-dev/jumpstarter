# Data Model: Driver Registration Mapping

## Entry-Point Structure

Each driver is registered via a setuptools entry-point in the `jumpstarter.drivers`
group. The registration lives in `pyproject.toml` under:

```toml
[project.entry-points."jumpstarter.drivers"]
EntryPointName = "python_module.driver:ClassName"
```

At runtime, `importlib.metadata.entry_points(group="jumpstarter.drivers")` returns
`EntryPoint` objects with `.name` (the left side) and `.value` (the right side).

## Registration Mapping for Missing Packages

### jumpstarter-driver-ble

```toml
[project.entry-points."jumpstarter.drivers"]
BleWriteNotifyStream = "jumpstarter_driver_ble.driver:BleWriteNotifyStream"
```

### jumpstarter-driver-flashers

```toml
[project.entry-points."jumpstarter.drivers"]
TIJ784S4Flasher = "jumpstarter_driver_flashers.driver:TIJ784S4Flasher"
RCarS4Flasher = "jumpstarter_driver_flashers.driver:RCarS4Flasher"
```

### jumpstarter-driver-http

```toml
[project.entry-points."jumpstarter.drivers"]
HttpServer = "jumpstarter_driver_http.driver:HttpServer"
```

### jumpstarter-driver-http-power

```toml
[project.entry-points."jumpstarter.drivers"]
HttpPower = "jumpstarter_driver_http_power.driver:HttpPower"
```

### jumpstarter-driver-iscsi

```toml
[project.entry-points."jumpstarter.drivers"]
ISCSI = "jumpstarter_driver_iscsi.driver:ISCSI"
```

### jumpstarter-driver-probe-rs

```toml
[project.entry-points."jumpstarter.drivers"]
ProbeRs = "jumpstarter_driver_probe_rs.driver:ProbeRs"
```

### jumpstarter-driver-pyserial

```toml
[project.entry-points."jumpstarter.drivers"]
PySerial = "jumpstarter_driver_pyserial.driver:PySerial"
```

### jumpstarter-driver-qemu

```toml
[project.entry-points."jumpstarter.drivers"]
QemuFlasher = "jumpstarter_driver_qemu.driver:QemuFlasher"
QemuPower = "jumpstarter_driver_qemu.driver:QemuPower"
Qemu = "jumpstarter_driver_qemu.driver:Qemu"
```

### jumpstarter-driver-ridesx

```toml
[project.entry-points."jumpstarter.drivers"]
RideSXDriver = "jumpstarter_driver_ridesx.driver:RideSXDriver"
RideSXPowerDriver = "jumpstarter_driver_ridesx.driver:RideSXPowerDriver"
```

### jumpstarter-driver-snmp

```toml
[project.entry-points."jumpstarter.drivers"]
SNMPServer = "jumpstarter_driver_snmp.driver:SNMPServer"
```

### jumpstarter-driver-ssh

```toml
[project.entry-points."jumpstarter.drivers"]
SSHWrapper = "jumpstarter_driver_ssh.driver:SSHWrapper"
```

### jumpstarter-driver-tftp

```toml
[project.entry-points."jumpstarter.drivers"]
Tftp = "jumpstarter_driver_tftp.driver:Tftp"
```

### jumpstarter-driver-tmt

```toml
[project.entry-points."jumpstarter.drivers"]
TMT = "jumpstarter_driver_tmt.driver:TMT"
```

### jumpstarter-driver-uboot

```toml
[project.entry-points."jumpstarter.drivers"]
UbootConsole = "jumpstarter_driver_uboot.driver:UbootConsole"
```

### jumpstarter-driver-ustreamer

```toml
[project.entry-points."jumpstarter.drivers"]
UStreamer = "jumpstarter_driver_ustreamer.driver:UStreamer"
```

## Excluded Packages

| Package | Reason for exclusion |
|---------|---------------------|
| jumpstarter-driver-opendal | Registers under `jumpstarter.adapters`, not `jumpstarter.drivers` |
| jumpstarter-driver-uds | Abstract interface only; concrete drivers in uds-can and uds-doip |
