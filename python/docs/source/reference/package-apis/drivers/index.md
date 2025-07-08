# Driver Packages

This section documents the drivers from the Jumpstarter packages directory. Each
driver is contained in a separate package in the form of
`jumpstarter-driver-{name}` and provides specific functionality for interacting
with different hardware components and systems.

## Types of Drivers

Jumpstarter includes several types of drivers organized by their primary
function:

### System Control Drivers

Drivers that control the power state and basic operation of devices:

* **[Power](power.md)** (`jumpstarter-driver-power`) - Power control for devices
* **[Raspberry Pi](raspberrypi.md)** (`jumpstarter-driver-raspberrypi`) -
  Raspberry Pi hardware control
* **[Yepkit](yepkit.md)** (`jumpstarter-driver-yepkit`) - Yepkit hardware
  control
* **[DUT Link](dutlink.md)** (`jumpstarter-driver-dutlink`) - [DUT Link
  Board](https://github.com/jumpstarter-dev/dutlink-board) hardware control
* **[Energenie PDU](energenie.md)** (`jumpstarter-driver-energenie`) - Energenie PDUs
* **[Tasmota](tasmota.md)** (`jumpstarter-driver-tasmota`) - Tasmota hardware control
* **[HTTP Power](http-power.md)** (`jumpstarter-driver-http-power`) - HTTP-based power
  control, useful for smart sockets, like the Shelly Smart Plug or similar

### Communication Drivers

Drivers that provide various communication interfaces:

* **[CAN](can.md)** (`jumpstarter-driver-can`) - Controller Area Network
  communication
* **[HTTP](http.md)** (`jumpstarter-driver-http`) - HTTP communication
* **[Network](network.md)** (`jumpstarter-driver-network`) - Network interfaces
  and configuration
* **[PySerial](pyserial.md)** (`jumpstarter-driver-pyserial`) - Serial port
  communication
* **[SNMP](snmp.md)** (`jumpstarter-driver-snmp`) - Simple Network Management
  Protocol
* **[TFTP](tftp.md)** (`jumpstarter-driver-tftp`) - Trivial File Transfer
  Protocol

### Storage and Data Drivers

Drivers that control storage devices and manage data:

* **[OpenDAL](opendal.md)** (`jumpstarter-driver-opendal`) - Open Data Access
  Layer
* **[SD Wire](sdwire.md)** (`jumpstarter-driver-sdwire`) - SD card switching
  utilities

### Media Drivers

Drivers that handle media streams:

* **[UStreamer](ustreamer.md)** (`jumpstarter-driver-ustreamer`) - Video
  streaming functionality

### Debug and Programming Drivers

Drivers for debugging and programming devices:

* **[Flashers](flashers.md)** (`jumpstarter-driver-flashers`) - Flash memory
  programming tools
* **[Probe-RS](probe-rs.md)** (`jumpstarter-driver-probe-rs`) - Debugging probe
  support
* **[QEMU](qemu.md)** (`jumpstarter-driver-qemu`) - QEMU virtualization platform
* **[Corellium](corellium.md)** (`jumpstarter-driver-corellium`) - Corellium
  virtualization platform
* **[U-Boot](uboot.md)** (`jumpstarter-driver-uboot`) - Universal Bootloader
  interface
* **[RideSX](ridesx.md)** (`jumpstarter-driver-ridesx`) - Flashing and power management for Qualcomm RideSX devices

### Utility Drivers

General-purpose utility drivers:

* **[Shell](shell.md)** (`jumpstarter-driver-shell`) - Shell command execution

```{toctree}
:hidden:
can.md
corellium.md
dutlink.md
energenie.md
flashers.md
http.md
http-power.md
network.md
opendal.md
power.md
probe-rs.md
pyserial.md
qemu.md
raspberrypi.md
ridesx.md
sdwire.md
shell.md
snmp.md
tasmota.md
tftp.md
uboot.md
ustreamer.md
yepkit.md
```
