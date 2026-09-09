# ADB Driver

`jumpstarter-driver-adb` carries the Android Debug Bridge protocol between a remote
Android device and your workstation, so your **own** `adb` — and Android Studio,
tradefed, gradle — can drive a device on someone else's bench.

## How it works

Devices are **declared** in the exporter config, one driver instance per device, and
plugged into the exporter over USB (or reachable at their own TCP address). Jumpstarter
moves the ADB protocol to your machine; ADB does everything else.

```text
DUT ──USB──▶ EXPORTER ──Jumpstarter tunnel──▶ YOU
             (owns the USB                    (your own adb,
              connection)                      Studio, tradefed…)
```

Two things worth knowing up front, because they shape the whole design:

**Jumpstarter does not wrap the `adb` CLI.** There is no `j adb shell`, no
`j adb install`. You already have `adb`; the driver's job is to hand you an address
and get out of the way. `attach` runs a single `adb connect` for convenience, and
`endpoint` just prints the address so you can drive adb yourself.

**Devices are declared, not discovered.** Each device is named in the exporter config
by the **bench USB port** it is plugged into. That is what makes a DUT composable with
its power relay and its console, so leasing the DUT leases the right device — and it
survives the two things that break auto-discovery: a relay power-cycle that
re-enumerates USB and changes the device's serial, and swapping hardware between
benches.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-adb
```

For the optional Python ADB API:

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ "jumpstarter-driver-adb[python-api]"
```

## Configuration

A two-DUT bench, each DUT with its own power relay:

```yaml
export:
  dut1:
    type: jumpstarter_driver_composite.driver.Composite
    children:
      power:
        type: jumpstarter_driver_yepkit.driver.Ykush
        config: { serial: "YK112233", port: "1" }
      adb:
        type: jumpstarter_driver_adb.driver.AdbDevice
        config:
          usb_port: "1-4.2"      # the bench port: swap the DUT, config unchanged

  dut2:
    type: jumpstarter_driver_composite.driver.Composite
    children:
      power:
        type: jumpstarter_driver_yepkit.driver.Ykush
        config: { serial: "YK112233", port: "2" }
      adb:
        type: jumpstarter_driver_adb.driver.AdbDevice
        config:
          usb_port: "1-4.3"

  # A device whose adbd already listens on TCP: a networked or AAOS head unit,
  # or a virtual device.
  dut3:
    type: jumpstarter_driver_adb.driver.AdbDevice
    config:
      transport: tcp
      address: "10.0.0.5:5555"
```

Note there is **no ADB server entry**. The server is implicit: the first device to
need one starts or adopts it, and every device on the same port shares it. Declare an
`AdbServer` only if you want the server-level CLI (`j adb devices`, `j adb tunnel`).

### Finding a device's `usb_port`

```console
$ adb devices -l
List of devices attached
HVA1234567    device usb:1-4.2 product:sdk model:Pixel device:generic
```

The `usb:` field is the value to put in `usb_port` — with or without the `usb:`
prefix, both are accepted. On Linux it is the kernel's bus-port path (`1-4.2`), which
is stable for a given physical port. On macOS it is an IOKit location ID in hex
instead, and its exact form depends on which USB backend adb uses (`ADB_LIBUSB`), so
exporters are expected to be Linux.

### `AdbDevice` parameters

| Parameter | Description | Type | Required | Default |
| --- | --- | --- | --- | --- |
| transport | `usb` for a USB-attached device, `tcp` for one whose adbd already listens on TCP | str | no | `usb` |
| usb_port | **usb:** the bench USB port, as reported by `adb devices -l` | str | one of usb_port/serial | — |
| serial | **usb:** an explicit ADB serial, for hardware with no usable USB devpath | str | one of usb_port/serial | — |
| address | **tcp:** the device's own adbd endpoint, `host` or `host:port` | str | yes for tcp | — |
| adbd_port | adbd's TCP port on the device (`persist.adb.tcp.port`); also the default port for `address` | int | no | 5555 |
| adb_path | Path to the ADB executable on the exporter | str | no | `adb` (resolved from PATH) |
| connect_timeout | Timeout (seconds) for adb commands | float | no | 30.0 |
| server_port | Which ADB server to use. Rarely set — the server is implicit and shared | int | no | 15037 |
| adopt_existing_server | Use an ADB server already listening on `server_port` rather than starting another | bool | no | true |

Prefer `usb_port` over `serial`. A serial identifies *a device*; the bench port
identifies *a position*, which is what stays true when hardware is swapped or a power
cycle changes the serial.

### `AdbServer` parameters

Optional. Declare it to point your tooling at the exporter's ADB server, or to get the
server-level CLI.

| Parameter | Description | Type | Required | Default |
| --- | --- | --- | --- | --- |
| adb_path | Path to the ADB executable on the exporter | str | no | `adb` (resolved from PATH) |
| host | Host address of the ADB server on the exporter | str | no | `127.0.0.1` |
| port | Port of the ADB server on the exporter | int | no | 15037 |
| connect_timeout | Timeout (seconds) for adb commands | float | no | 30.0 |
| adopt_existing_server | Use an ADB server already listening on `port` instead of starting another | bool | no | true |

### Running the exporter in a container

adb finds USB devices by walking `/dev/bus/usb` and **rejects any path component that
is not all digits**, so it only ever looks at real `/dev/bus/usb/<bus>/<dev>` nodes. A
friendly `/dev` symlink is therefore useful for the `podman run` line — Podman
resolves a symlinked `--device` and stores only the major/minor — but adb itself will
never see that name. Pass the device through at its real path:

```shell
podman run --device /dev/bus/usb/001/017 ...
```

Permissions matter, and udev is the right place for them: adb falls back to read-only
(and cannot talk to the device) if it cannot open the node `O_RDWR`. A rule granting
your exporter's user or group access to the DUT's vendor ID is the usual fix.

Passing exactly one device into a container also isolates it: that container's ADB
server can only ever see the device you gave it.

### An ADB server already running on the exporter

An ADB server **claims** the USB devices it finds, and only one server can hold a
given device. So if a server is already listening on the driver's port — started by
hand, by udev, by a previous run, or by a developer working on the exporter directly —
a second one does not give a second view of those devices. It gives an *empty* one,
and `adb start-server` reports success either way, so the driver would come up seeing
no devices at all while looking healthy.

By default the driver therefore **adopts** a server already on its port, and leaves it
running at teardown rather than killing a server other processes are using. For the
same reason, every `AdbDevice` on a given port shares one server rather than each
starting its own.

If something that is *not* an ADB server holds the port, the driver declines to adopt
it and logs a warning. This matters because `adb start-server` and `adb devices` both
block forever against such a listener rather than failing, so all of the driver's adb
calls are bounded by `connect_timeout`.

### Port assignment

The exporter runs its ADB server on a non-standard port (default 15037) so it cannot
collide with the standard 5037 — which matters because Android Studio starts and
maintains a server there and will restart it if killed.

Exporter-side forward ports are **not** configured: each forward is created as
`adb forward tcp:0`, so the ADB server picks a free port and the driver adopts
whatever it chose. Nothing on the exporter has to be kept clear of a guessed range.

## Usage

### Attach a device to your own ADB server

```console
$ j dut1.adb attach
attached as 127.0.0.1:41000

Your ADB server now lists it; use it with:  adb -s 127.0.0.1:41000 shell
Android Studio will list it too.

Press Ctrl+C to detach
```

Leave it running for as long as you want the device available. Then, in another
terminal, it is just adb:

```shell
adb -s 127.0.0.1:41000 shell
adb -s 127.0.0.1:41000 install app.apk
adb -s 127.0.0.1:41000 logcat
adb -s 127.0.0.1:41000 push local_file.txt /sdcard/
```

Because `adb connect` is **additive**, the device joins whatever your ADB server
already holds — your own emulator, another bench, a phone — and every Android tool
sees it with no configuration. You do not need to own your ADB server, and you do not
need one at all: `adb connect` starts one if none is running.

### Just give me the address

`attach` is a convenience. If you would rather drive adb yourself, or point a tool
that takes a `host:port` at the device:

```console
$ j dut1.adb endpoint
127.0.0.1:41000

Add it to your ADB server with:  adb connect 127.0.0.1:41000
Press Ctrl+C to stop
```

No adb runs on your machine at all. This is the primitive the rest is built on.

### Is my device there?

```console
$ j dut1.adb info
transport: usb
adbd_port: 5555
selector: usb:1-4.2
serial: HVA1234567
present: yes
```

A declared device that is powered off reports `present: no` with the reason. That is
normal, not an error — the exporter starts fine with every DUT powered down, and the
device is picked up the moment its relay turns on.

### Requirements and limits

- The device's `adbd` must listen on TCP (`persist.adb.tcp.port`, commonly 5555). A
  stock phone needs `adb tcpip 5555` first — note this restarts `adbd` and may drop
  the USB connection.
- The local address (`127.0.0.1:<port>`) is assigned per session and is not stable
  across sessions. Anything that remembers a device by address (a saved Android Studio
  run target) needs re-selecting after re-attaching. Use `-P` to pin the port if you
  need one address to stay put.
- Direct mode has no lease arbitration, so two clients attaching the same device will
  interfere. Use distributed mode for a shared fleet.

### Power cycles and re-enumeration

Nothing to configure: the device's serial and its forward are resolved fresh on every
connection. A relay power-cycle re-enumerates USB and can hand the device a different
ADB serial, and the old forward disappears with it — the driver notices, re-resolves
the declared `usb_port` to the new serial, and forwards again. No config edit, no
exporter restart.

Re-attach after the DUT is back up; the local address will generally be a new port.

## Transports

| | `usb` | `tcp` |
| --- | --- | --- |
| The device is | plugged into the exporter over USB | listening on its own TCP address |
| Identified by | `usb_port` (or `serial`) | `address` |
| On the exporter | `adb forward tcp:0 tcp:5555` | `adb connect <address>` |
| Typical case | a bench DUT on a relay | AAOS head unit, networked or virtual device |

### There is no serial/UART transport

adb has none, so neither does this driver. Confirmed in AOSP: `adb.h` defines only
`kTransportUsb` and `kTransportLocal` (where "local" means TCP), and `connect_device()`
coerces every address to `tcp:` — `adb connect serial:/dev/ttyUSB0` fails with
`bad port number '/dev/ttyUSB0'`. The `dev:` and `dev-raw:` specs that appear in
`adb help` are **forward targets executed inside adbd on the device**, not host
transports. Device-side there is no adbd-over-UART property either;
`ttyGS0`/gadget-serial gives a serial console, not an adb transport.

To reach a serial-only DUT, get it onto TCP and use `transport: tcp`: either use its
console to enable adbd over TCP (`setprop service.adb.tcp.port 5555; stop adbd; start
adbd`), or bridge the UART to a TCP port outside Jumpstarter. Be aware that a raw UART
gives adb no retransmission and no checksum, so hardware flow control is mandatory,
the line must not be shared with a kernel console or getty, and at 115200 baud you get
~11.5 KB/s — enough for a shell, not for `push` or `bugreport`.

## Pointing your tools at the exporter's ADB server

The opposite model to `attach`: instead of adding one device to *your* server, aim
your tools at the exporter's server and see its devices instead of your own. Right for
CI, a headless runner, or a container. Requires a declared `AdbServer`.

```console
$ j adb tunnel
ADB server tunneled to 127.0.0.1:54321

To use your own adb or other tools, run:
  export ANDROID_ADB_SERVER_ADDRESS=127.0.0.1
  export ANDROID_ADB_SERVER_PORT=54321

Press Ctrl+C to stop
```

This replaces your server rather than adding to it, which is exactly wrong when an IDE
is running — Android Studio owns 5037 and respawns its server there within ~3s of
being killed, so the port cannot reliably be taken over. Use `attach` in that case.

## Integration with Android ecosystem tools

### How this relates to Android's own remote-device support

`attach` is deliberately the same shape as the remote-device flow Google documents, so
Android Studio needs no Jumpstarter-specific support:

- Android's [wireless debugging](https://developer.android.com/tools/adb) has you run
  `adb tcpip 5555` then `adb connect <ip>:5555`, and the device then appears as a
  `host:port` serial alongside your emulators. `attach` does exactly that, except the
  `host:port` is a local tunnel endpoint rather than the device's own IP — which is
  what makes it work when the device is on a bench network you cannot route to.
- Because the ADB server "manages connections to devices and handles commands from
  multiple `adb` clients", remote and local devices coexist and are addressed with
  `-s <serial>` (or `$ANDROID_SERIAL`) in the ordinary way. Nothing about a
  Jumpstarter-attached device is special to a client.

Two deliberate differences: there is **no pairing** (the tunnel exists only for the
lease, so there is nothing to remember or revoke — lease lifetime is the security
boundary), and **the address is not stable** across sessions.

### Android Studio

Run `j dut1.adb attach`. The device appears in Studio's device chooser with **no
configuration**: no `adb.server.port`, no environment variables, no restart. Leave the
command running for as long as you want the device available.

### Trade Federation (tradefed)

tradefed discovers devices through the ADB server, so an attached device is visible to
it with no extra setup:

```shell
j dut1.adb attach          # leave running
tradefed.sh
# > list devices          <-- shows the attached device
```

To give tradefed the exporter's whole device list instead, use `j adb tunnel` and
export `ANDROID_ADB_SERVER_PORT`.

### Python API

Drive a device programmatically with [`adbutils`](https://github.com/openatx/adbutils)
against the endpoint, no CLI involved:

```python
# Requires: pip install jumpstarter-driver-adb[python-api]
import adbutils

with client.dut1.adb.endpoint() as target:
    host, port = target.rsplit(":", 1)
    adb = adbutils.AdbClient(host=host, port=int(port))
    print(adb.device().prop.model)
```

### Connecting the exporter's server to a networked device

For a device the *exporter* should `adb connect` to — a Cuttlefish instance, say —
`AdbServer` exposes `connect_device` / `disconnect_device`. The address is supplied by
the caller; this driver does not discover or scan for devices. A parent composite
driver that owns the device lifecycle is the intended user: the Cuttlefish driver
embeds an `AdbServer` child and connects to an address derived from its own config.

For a networked device you want in *your* ADB server, prefer an `AdbDevice` with
`transport: tcp` — it needs no parent driver.

## CLI

### Per-device (`j <device>.adb ...`)

| Usage | Description |
| --- | --- |
| `j <device>.adb attach` | Add this device to your own ADB server; holds until Ctrl+C |
| `j <device>.adb endpoint` | Print the device's local adbd address; holds until Ctrl+C |
| `j <device>.adb info` | Show the device's transport, selector and whether it is present |

Options for `attach` and `endpoint`:

| Option | Description | Default |
| --- | --- | --- |
| `-H HOST` | Local address to bind | 127.0.0.1 |
| `-P PORT` | Local port to bind (0=auto) | 0 |
| `--adb PATH` | Path to your local adb (`attach` only) | adb |

### Server-level (`j adb ...`, needs a declared `AdbServer`)

| Usage | Description |
| --- | --- |
| `j adb devices` | List devices visible to the exporter's ADB server |
| `j adb tunnel [-H HOST] [-P PORT]` | Forward the exporter's ADB server to a local port; holds until Ctrl+C |

Everything else is your own `adb`, run directly.

## API Reference

### Device driver

```{eval-rst}
.. autoclass:: jumpstarter_driver_adb.driver.AdbDevice()
    :members: connect, info
```

### Server driver

```{eval-rst}
.. autoclass:: jumpstarter_driver_adb.driver.AdbServer()
    :members: list_devices, start_server, kill_server, connect_device, disconnect_device
```

### Device client

```{eval-rst}
.. autoclass:: jumpstarter_driver_adb.client.AdbDeviceClient()
    :members: attach, endpoint, info
```

### Server client

```{eval-rst}
.. autoclass:: jumpstarter_driver_adb.client.AdbClient()
    :members: forward_adb, devices, list_devices, connect_device, disconnect_device
```
