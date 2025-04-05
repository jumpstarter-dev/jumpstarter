# Driver Packages

This section documents the drivers from the Jumpstarter packages directory. Each driver is contained in a separate package in the form of `jumpstarter-driver-{name}` and provides specific functionality for interacting with different hardware components and systems.

## Types of Drivers

Jumpstarter includes several types of drivers organized by their primary function:

### System Control Drivers
Drivers that control the power state and basic operation of devices:

* **[Power](/api-reference/drivers/power.md)** (`jumpstarter-driver-power`) - Power control for devices
* **[Raspberry Pi](/api-reference/drivers/raspberrypi.md)** (`jumpstarter-driver-raspberrypi`) - Raspberry Pi hardware control
* **[Yepkit](/api-reference/drivers/yepkit.md)** (`jumpstarter-driver-yepkit`) - Yepkit hardware control
* **[DUT Link](/api-reference/drivers/dutlink.md)** (`jumpstarter-driver-dutlink`) - [DUT Link Board](https://github.com/jumpstarter-dev/dutlink-board) hardware control

### Communication Drivers
Drivers that provide various communication interfaces:

* **[CAN](/api-reference/drivers/can.md)** (`jumpstarter-driver-can`) - Controller Area Network communication
* **[D-Bus](/api-reference/drivers/dbus.md)** (`jumpstarter-driver-dbus`) - D-Bus message system interface
* **[HTTP](/api-reference/drivers/http.md)** (`jumpstarter-driver-http`) - HTTP communication
* **[Network](/api-reference/drivers/network.md)** (`jumpstarter-driver-network`) - Network interfaces and configuration
* **[Proxy](/api-reference/drivers/proxy.md)** (`jumpstarter-driver-proxy`) - Network proxy functionality
* **[PySerial](/api-reference/drivers/pyserial.md)** (`jumpstarter-driver-pyserial`) - Serial port communication
* **[SNMP](/api-reference/drivers/snmp.md)** (`jumpstarter-driver-snmp`) - Simple Network Management Protocol
* **[TFTP](/api-reference/drivers/tftp.md)** (`jumpstarter-driver-tftp`) - Trivial File Transfer Protocol

### Storage and Data Drivers
Drivers that control storage devices and manage data:

* **[OpenDAL](/api-reference/drivers/opendal.md)** (`jumpstarter-driver-opendal`) - Open Data Access Layer
* **[SD Wire](/api-reference/drivers/sdwire.md)** (`jumpstarter-driver-sdwire`) - SD card switching utilities

### Media Drivers
Drivers that handle media streams:

* **[UStreamer](/api-reference/drivers/ustreamer.md)** (`jumpstarter-driver-ustreamer`) - Video streaming functionality

### Debug and Programming Drivers
Drivers for debugging and programming devices:

* **[Flashers](/api-reference/drivers/flashers.md)** (`jumpstarter-driver-flashers`) - Flash memory programming tools
* **[Probe-RS](/api-reference/drivers/probe-rs.md)** (`jumpstarter-driver-probe-rs`) - Debugging probe support
* **[QEMU](/api-reference/drivers/qemu.md)** (`jumpstarter-driver-qemu`) - QEMU virtualization platform
* **[U-Boot](/api-reference/drivers/uboot.md)** (`jumpstarter-driver-uboot`) - Universal Bootloader interface

### Utility Drivers
General-purpose utility drivers:

* **[Shell](/api-reference/drivers/shell.md)** (`jumpstarter-driver-shell`) - Shell command execution

```{toctree}
:hidden:
can.md
dbus.md
dutlink.md
flashers.md
http.md
network.md
opendal.md
power.md
proxy.md
probe-rs.md
pyserial.md
qemu.md
raspberrypi.md
sdwire.md
shell.md
snmp.md
tftp.md
uboot.md
ustreamer.md
yepkit.md
```
