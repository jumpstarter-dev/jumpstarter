# DUT Link Driver

`jumpstarter-driver-dutlink` provides functionality for interacting with DUT
Link devices.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-dutlink
```

## Configuration

Example configuration:

```yaml
export:
  dutlink:
    type: jumpstarter_driver_dutlink.driver.Dutlink
    config:
      storage_device: "/dev/disk/by-id/usb-SanDisk_3.2_Gen_1_54345678AE6C-0:0"
      serial: "e6058905" # Optional: pick one board when several are connected
```

| Parameter | Description | Default |
|---|---|---|
| `storage_device` | Block device of the storage mux as the exporter host sees it. Prefer a `/dev/disk/by-id/` or `/dev/disk/by-path/` path over `/dev/sdX`, which can change between boots. Required, even when the mux is not used. | |
| `serial` | USB serial number of the board: the `ID_SERIAL_SHORT` property in `udevadm info -q property -n /dev/ttyACM0`. Without it, the first board found is used. | `None` |
| `alternate_console` | Serial port to use as the console instead of the board's own, for example `/dev/serial/by-id/...`. | `None` |
| `baudrate` | Console baud rate. | `115200` |
| `timeout_s` | Timeout for USB control transfers, in seconds. Power sequences can block for a long time. | `20` |

The driver does not power off the DUT or switch the storage mux when a lease
starts or ends, so the board stays as the last client left it. To power it off
between leases, run `j dutlink power off` in an `afterLease`
{doc}`hook <../../../introduction/hooks>`.

The console gets the board's power control as its `power` child, so pressing
Ctrl-] three times in `j dutlink console console` power cycles the DUT (see the
{doc}`PySerial driver <pyserial>`).

### Firmware

Power and storage control need DUT Link firmware 0.08 or later: older firmware has
no control interface, and the driver then fails at the first power or storage call
with an error naming the firmware version. The console works with older firmware.
Releases and flashing instructions are at
<https://github.com/jumpstarter-dev/dutlink-firmware>.

### Permissions

The exporter opens the board over libusb and opens its console tty. To run it as a
non-root user, add a udev rule such as
`/etc/udev/rules.d/60-dutlink.rules`:

```text
SUBSYSTEM=="usb", ATTR{idVendor}=="2b23", ATTR{idProduct}=="1012", MODE="0660", GROUP="plugdev"
SUBSYSTEM=="tty", ATTRS{idVendor}=="2b23", ATTRS{idProduct}=="1012", MODE="0660", GROUP="plugdev"
```

The rules grant access to the `plugdev` group, so the account that runs the
exporter must be a member. If your distribution has no `plugdev` group, create it
or use a group that account already belongs to. A new group membership applies to
new login sessions, so log in again or restart the exporter service after adding
it.

Then run `sudo udevadm control --reload` and re-plug the board. Without the first
line, the exporter fails with `The device has no langid (permission issue, ...)`;
without the second, with `Permission denied` on the console tty.

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_dutlink.driver.Dutlink()
```
