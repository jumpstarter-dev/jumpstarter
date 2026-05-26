# Drivers

This section documents the drivers from the Jumpstarter packages directory. Each
driver is contained in a separate package in the form of
`jumpstarter-driver-{name}` and provides specific functionality for interacting
with different hardware components and systems.

## Types of Drivers

Jumpstarter includes several types of drivers organized by their primary
function:

### System Control

Drivers that control the power state and basic operation of devices:

- **[Power](power.md)** (`jumpstarter-driver-power`) - Power control for devices
- **[gpiod](gpiod.md)** (`jumpstarter-driver-gpiod`) - GPIO hardware control via libgpiod
- **[Yepkit](yepkit.md)** (`jumpstarter-driver-yepkit`) - Yepkit USB hub hardware control
- **[DUT Link](dutlink.md)** (`jumpstarter-driver-dutlink`) - [DUT Link Board](https://github.com/jumpstarter-dev/dutlink-board) hardware control
- **[Energenie PDU](energenie.md)** (`jumpstarter-driver-energenie`) - Energenie PDU control
- **[Tasmota](tasmota.md)** (`jumpstarter-driver-tasmota`) - Tasmota device control
- **[HTTP Power](http-power.md)** (`jumpstarter-driver-http-power`) - HTTP-based power control for smart sockets
- **[Noyito Relay](noyito-relay.md)** (`jumpstarter-driver-noyito-relay`) - NOYITO USB relay board control

### Communication

Drivers that provide various communication interfaces:

- **[ADB](adb.md)** (`jumpstarter-driver-adb`) - Android Debug Bridge tunneling
- **[BLE](ble.md)** (`jumpstarter-driver-ble`) - Bluetooth Low Energy communication
- **[CAN](can.md)** (`jumpstarter-driver-can`) - Controller Area Network communication
- **[HTTP](http.md)** (`jumpstarter-driver-http`) - HTTP communication
- **[mitmproxy](mitmproxy.md)** (`jumpstarter-driver-mitmproxy`) - HTTP/HTTPS interception, mocking, and traffic recording
- **[DUT Network](dut-network.md)** (`jumpstarter-driver-dut-network`) - DUT network isolation with bridge, DHCP, DNS, and NAT
- **[Network](network.md)** (`jumpstarter-driver-network`) - Network interfaces and configuration
- **[PySerial](pyserial.md)** (`jumpstarter-driver-pyserial`) - Serial port communication
- **[SNMP](snmp.md)** (`jumpstarter-driver-snmp`) - Simple Network Management Protocol
- **[SSH](ssh.md)** (`jumpstarter-driver-ssh`) - SSH wrapper driver
- **[SSH MITM](ssh-mitm.md)** (`jumpstarter-driver-ssh-mitm`) - SSH proxy with server-side private key storage
- **[TFTP](tftp.md)** (`jumpstarter-driver-tftp`) - Trivial File Transfer Protocol
- **[VNC](vnc.md)** (`jumpstarter-driver-vnc`) - Virtual Network Computing remote desktop
- **[XCP](xcp.md)** (`jumpstarter-driver-xcp`) - Universal Measurement and Calibration Protocol

### Storage and Data

Drivers that control storage devices and manage data:

- **[OpenDAL](opendal.md)** (`jumpstarter-driver-opendal`) - Open Data Access Layer
- **[SD Wire](sdwire.md)** (`jumpstarter-driver-sdwire`) - SD card switching
- **[iSCSI](iscsi.md)** (`jumpstarter-driver-iscsi`) - iSCSI target server for LUN export

### Media

Drivers that handle media streams:

- **[uStreamer](ustreamer.md)** (`jumpstarter-driver-ustreamer`) - Video streaming

### Automotive Diagnostics

Drivers for automotive diagnostic protocols:

- **[DoIP](doip.md)** (`jumpstarter-driver-doip`) - Diagnostics over Internet Protocol (ISO 13400)
- **[UDS](uds.md)** (`jumpstarter-driver-uds`) - Unified Diagnostic Services (ISO 14229)
- **[UDS over DoIP](uds-doip.md)** (`jumpstarter-driver-uds-doip`) - UDS diagnostics over DoIP transport
- **[UDS over CAN](uds-can.md)** (`jumpstarter-driver-uds-can`) - UDS diagnostics over CAN/ISO-TP transport
- **[SOME/IP](someip.md)** (`jumpstarter-driver-someip`) - SOME/IP protocol operations via opensomeip

### Flashing and Programming

Drivers for flashing firmware and programming devices:

- **[ESP32](esp32.md)** (`jumpstarter-driver-esp32`) - ESP32 flashing via esptool
- **[Flashers](flashers.md)** (`jumpstarter-driver-flashers`) - Flash memory programming tools
- **[Pi Pico](pi-pico.md)** (`jumpstarter-driver-pi-pico`) - Raspberry Pi Pico UF2 flashing via BOOTSEL
- **[Probe-RS](probe-rs.md)** (`jumpstarter-driver-probe-rs`) - Debug probe support
- **[ST-LINK MSD](stlink-msd.md)** (`jumpstarter-driver-stlink-msd`) - ST-LINK mass storage flasher for STM32
- **[U-Boot](uboot.md)** (`jumpstarter-driver-uboot`) - Universal Bootloader interface
- **[RideSX](ridesx.md)** (`jumpstarter-driver-ridesx`) - Flashing and power management for Qualcomm RideSX

### Emulation

Drivers for virtual and emulated targets:

- **[Android Emulator](androidemulator.md)** (`jumpstarter-driver-androidemulator`) - Android emulator lifecycle management with ADB tunneling
- **[QEMU](qemu.md)** (`jumpstarter-driver-qemu`) - QEMU virtual machine management
- **[Renode](renode.md)** (`jumpstarter-driver-renode`) - Renode embedded systems emulation
- **[Corellium](corellium.md)** (`jumpstarter-driver-corellium`) - Corellium virtualization platform

### Utility

General-purpose utility drivers:

- **[Shell](shell.md)** (`jumpstarter-driver-shell`) - Shell command execution
- **[TMT](tmt.md)** (`jumpstarter-driver-tmt`) - Test Management Tool wrapper

```{toctree}
:hidden:
adb.md
androidemulator.md
ble.md
can.md
corellium.md
doip.md
dut-network.md
dutlink.md
energenie.md
esp32.md
flashers.md
gpiod.md
http.md
http-power.md
iscsi.md
mitmproxy.md
network.md
noyito-relay.md
opendal.md
pi-pico.md
power.md
probe-rs.md
pyserial.md
qemu.md
renode.md
ridesx.md
sdwire.md
shell.md
snmp.md
someip.md
ssh.md
ssh-mitm.md
stlink-msd.md
tasmota.md
tftp.md
tmt.md
uboot.md
uds.md
uds-can.md
uds-doip.md
ustreamer.md
vnc.md
xcp.md
yepkit.md
```
