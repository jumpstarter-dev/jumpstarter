# ADB Driver

`jumpstarter-driver-adb` carries the [Android Debug Bridge (ADB)](https://developer.android.com/tools/adb)
protocol between a remote Android device and a Jumpstarter client. This enables existing tools in the Android ecosystem
such as Android Studio to connect to a remote Android device as if it was physically
connected via USB.

## How it works

Android devices are **declared** in the exporter config, one driver instance per device, and
plugged into the exporter over USB (or reachable at their own TCP address). Jumpstarter
moves the ADB protocol to your machine; ADB does everything else.

```text
DUT ──USB──▶ EXPORTER ──Jumpstarter tunnel──▶ CLIENT
             (owns the USB                    (your own adb,
              connection)                      Studio, tradefed…)
```

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-adb
```

For the optional Python ADB API:

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ "jumpstarter-driver-adb[python-api]"
```

## Configuration

For example, a simple Android phone bench could be configured as follows:

```yaml
export:
  # An Android phone cabled directly to the exporter over USB
  phone:
    type: jumpstarter_driver_adb.driver.AdbDevice
    config:
      usb_port: "1-4.2"        # the physical USB port the device is plugged into

  # A device whose adbd already listens on TCP, for example a virtual Cuttlefish instance
  virtual:
    type: jumpstarter_driver_adb.driver.AdbDevice
    config:
      transport: tcp
      address: "10.0.0.5:5555"
```

### Finding a device's ADB USB port

#### Linux

On a Linux device, if ADB is installed, you can run the following command:

```console
$ adb devices -l
List of devices attached
HVA1234567    device usb:1-4.2 product:sdk model:Pixel device:generic
```

The `usb:` field is the value to put in `usb_port`. With or without the `usb:`
prefix, both are accepted. On Linux it is the kernel's bus-port path (`1-4.2`), which is
stable for a given physical port, so it is the identity a bench wants.

#### macOS

On macOS, the USB device is reported as the `IOKit` location ID:

```console
$ adb devices -l
List of devices attached
HVA1234567    device usb:538116096X product:sdk model:Pixel device:generic
```

Copy the `usb:` field verbatim, `X` suffix included — here `usb_port` is `538116096X`.

The `X` is not a hex marker. adb formats this value as decimal followed by a literal `X`,
so do not convert it to hex. The number itself is macOS's IOKit location ID, which
describes **where** the device sits in the USB tree rather than anything about the device:
it packs the controller and each hub port into one integer, so it plays the same role as
Linux's `1-1.1`.

That makes it stable for fixed wiring, but it is a path through your hubs — moving the
device to another port, or re-cabling a dock, changes it. It is also not readable at a
glance the way `1-4.2` is. Prefer Linux for production exporters and keep macOS for local
development.

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
| server_port | Which ADB server to use. By default, the adb server is automatically started and shared. | int | no | 15037 |
| adopt_existing_server | Use an ADB server already listening on `server_port` rather than starting another. With `false` the driver owns the server *only if the port was free* — see below | bool | no | true |

Always prefer the specific `usb_port` over a device `serial`.
A serial identifies *a specific device*; the port identifies *a USB port*, which stays consistent even if the DUT is swapped out.

### `AdbServer` parameters

Deprecated; prefer `AdbDevice`. Declare it to point your tooling at the exporter's whole
ADB server, or to get the server-level CLI.

| Parameter | Description | Type | Required | Default |
| --- | --- | --- | --- | --- |
| adb_path | Path to the ADB executable on the exporter | str | no | `adb` (resolved from PATH) |
| host | Host address of the ADB server on the exporter | str | no | `127.0.0.1` |
| port | Port of the ADB server on the exporter | int | no | 15037 |
| connect_timeout | Timeout (seconds) for adb commands | float | no | 30.0 |
| adopt_existing_server | Use an ADB server already listening on `port` instead of starting another. With `false`, ownership is taken only if the port was free (see `AdbDevice` above) | bool | no | true |

### Running the exporter in a container

An exporter in a rootless container needs access to one USB device, and only that one.

The obvious approach — `--device /dev/bus/usb/001/017` — does not survive normal use. The
`<dev>` number is reassigned whenever the device re-enumerates, which a power cycle or a
replug does, and neither Podman nor Docker can add a device to a running container. adb
also walks `/dev/bus/usb` itself and ignores any path component that is not all digits, so
a friendly symlink does not help either.

Mount the whole USB tree instead, so re-enumeration is followed automatically, and use a
`udev` rule to hand **one port** to the container's user. Values to substitute:

| Placeholder | Meaning | Example |
| --- | --- | --- |
| `<PORT>` | the USB port, the same value as `usb_port` in your exporter config | `1-4.2` |
| `<USER>` | the user the container runs as | `jumpstarter` |
| `<KEYDIR>` | host directory holding the exporter's ADB key | `/var/lib/jumpstarter/adb` |
| `<IMAGE>` | your exporter image | — |

**1. Give the port to your user.** Create `/etc/udev/rules.d/72-jumpstarter-adb.rules`:

```text
SUBSYSTEM=="usb", KERNEL=="<PORT>", ENV{DEVTYPE}=="usb_device", \
  TAG-="uaccess", TAG-="seat", OWNER="<USER>", GROUP="root", MODE="0600"
```

**2. Apply it**, then unplug and replug the device — udev sets node permissions when a
device appears, so the rule takes effect on the next enumeration:

```console
$ sudo udevadm control --reload
```

**3. Check that one port, and only that port, changed hands:**

```console
$ ls -l /dev/bus/usb/*/
crw-------  1 <USER> root  189, 31 ... 031   # your device
crw-rw-r--  1 root   root  189,  0 ... 001   # everything else, untouched
```

**4. Run the container:**

```shell
podman run -v /dev/bus/usb:/dev/bus/usb \
  -v <KEYDIR>:/root/.android \
  -v /etc/jumpstarter/exporter.yaml:/etc/jumpstarter/exporter.yaml:ro \
  <IMAGE>
```

`<KEYDIR>` matters: every ADB server generates its own `~/.android/adbkey`, and the device
trusts keys, not containers. Without a persistent directory you have to authorize the
debugging prompt again on every container restart.

**5. Confirm the container sees exactly one device:**

```console
$ podman run --rm -v /dev/bus/usb:/dev/bus/usb <IMAGE> adb devices -l
List of devices attached
<serial>    device usb:<PORT> ...
```

If it lists nothing, an ADB server on the *host* is usually holding the device, since only
one server can claim it. Stop that server, or point the driver at it with `server_port`.

#### Things that fail quietly

| Pitfall | What happens | Fix |
| --- | --- | --- |
| The rule file sorts after `73-seat-late.rules` | systemd tags removable devices `uaccess` and gives the logged-in user an ACL on **every** one, which overrides `MODE="0600"` | Name the file `72-` or lower, and keep the `TAG-=` entries |
| `--group-add keep-groups` | Restores *all* of the user's groups, the opposite of what you want here | Leave it off; grant access with `OWNER=` on the port |
| `<USER>` has no subuid range | Rootless Podman fails in `newuidmap` before it reaches the device | Add ranges for the user in `/etc/subuid` and `/etc/subgid` |

`--device` does work rootless — it is only a bind mount — but it pins the node number
rather than the port. The cgroup device rules that could express "this major:minor only"
need root.

#### Differences between distributions

The mechanism above is upstream udev and upstream Podman, so it is portable, but three
things differ by host and are worth checking before blaming the driver:

| Host | What changes |
| --- | --- |
| SELinux (Fedora, RHEL, CentOS Stream) | The bind mount keeps its SELinux label, so the container gets `Permission denied` on the device even with correct ownership. Run `sudo setsebool -P container_use_devices=true`. `--security-opt label=disable` also works but drops SELinux separation for the whole container. |
| Debian, Ubuntu, Raspberry Pi OS | Ship `51-android.rules`, which puts Android devices in `plugdev`. That is per-device, not per-port, so it grants more than a bench wants; the rule above overrides it because `/etc` files of a later name win. |
| No systemd-logind | No `uaccess` tag exists, so the `TAG-=` lines are harmless no-ops and `OWNER`/`MODE` apply directly. |

Rule ordering is not distribution-specific: udev collects `/etc/udev/rules.d`,
`/run/udev/rules.d` and `/usr/lib/udev/rules.d` and sorts them **lexicographically as one
set**, regardless of directory. A `72-` file in `/etc` therefore runs before
`73-seat-late.rules` in `/usr/lib` on any systemd host.

Two Podman details are easy to trip over. `--group-add keep-groups` only works with the
**crun** runtime and is silently unavailable under runc — one more reason to grant access
with `OWNER=` on the port instead. And `--userns=keep-id` is optional here: by default a
rootless container maps your user to *root inside the container*, which already owns the
bind-mounted node; use `keep-id` only if the image expects to run as a specific UID.

The `usb_port` value itself is per-host, since it describes your USB topology. Read it
from `adb devices -l` on the exporter rather than copying it between benches.

### An ADB server already running on the exporter

An ADB server **claims** the USB devices it finds, and only one server can hold a given
device. A second server on the same port therefore sees an *empty* device list — and
`adb start-server` exits 0 either way, so a driver that started one would look healthy
and see nothing at all.

The driver's behavior follows from that:

| Situation | What the driver does |
| --- | --- |
| A server is already listening on `server_port` | Adopts it, and leaves it running at teardown |
| Several `AdbDevice`s share a port | They share the one server; sharing is keyed on the **port**, not on `adb_path` |
| Two drivers name different `adb_path`s | They share it, with a warning: an `adb` client whose version differs from the running server kills and restarts it, dropping every device claim on that port |
| Something that is *not* an ADB server holds the port | Declines to adopt it, and warns. `adb start-server` and `adb devices` both block forever against such a listener, so every adb call is bounded by `connect_timeout` |

Teardown only undoes what the driver itself did, at the end of the lease that created it:

- A forward or an `adb connect` that was **already there is reused and left in place**.
  `adb forward --list` cannot say who created a forward, so removing one could break
  another lease — or a person at the bench — with no error reported anywhere.
- A forward the driver **did** create is re-checked before removal, because
  `adb forward --remove` matches on the local port alone and ignores `-s <serial>`. Another
  `adb forward` on that port silently rebinds it to a different device, so the driver
  removes it only while it still forwards this device.

### Port assignment

The exporter runs its ADB server on a non-standard port (default 15037) so it cannot
collide with the standard 5037. This is important because Android Studio starts and
maintains its own server on the default port.

Exporter-side forward ports are **not** configured: each forward is created as
`adb forward tcp:0`, so the ADB server picks a free port and the driver adopts
whatever it chose. Nothing on the exporter has to be kept clear of a guessed range.

## Upgrading from an earlier `AdbServer`

**Breaking change: the `j adb <command>` passthrough has been removed.** Any script or
CI job that ran `j adb shell`, `j adb logcat`, `j adb install` and so on needs updating —
see the table below. Nothing else about the CLI or the config is source-incompatible.

**Exporter configs need no changes.** `adb_path`, `host`, `port` (still 15037) and
`connect_timeout` keep their names, types and defaults. `adopt_existing_server` is new and
defaults to `true`.

Two behaviors changed, both in the safer direction:

| | Before | Now |
| --- | --- | --- |
| A server is already listening on `port` | A second one was started. `adb start-server` exits 0 either way, so the exporter came up healthy but seeing **no devices**, since only one server can hold a device | It is adopted |
| Exporter teardown | Always ran `adb kill-server`, dropping the device claims of everything else on the host — including an Android Studio server | Kills only a server it started itself |

If a bench previously "worked" but reported an empty device list, that was this bug and it
is now fixed. The unconditional kill is gone deliberately and cannot be restored:
`adopt_existing_server: false` asks for a server of the driver's own, but it still never
kills one it did not start.

**The `j adb <command>` passthrough has been removed.** Jumpstarter no longer wraps the adb
CLI; it gives you an address and you use your own adb:

| Before | Now |
| --- | --- |
| `j adb devices` | unchanged |
| `j adb shell`, `j adb logcat`, any other passthrough | `j <device>.adb connect`, then your own `adb -s <address> shell` |
| pointing tools at the exporter's whole server | `j adb serve`, then export the printed `ANDROID_ADB_SERVER_*` variables |

**Drivers that embed `AdbServer`** — such as the Cuttlefish and Android emulator drivers —
need no changes. `start_server`, `kill_server`, `connect_device`, `disconnect_device`,
`list_devices` and `adb_env` keep their signatures and their failure behavior. The only
difference to be aware of is that `close()` may now leave the server running.

## Usage

### Starting the exporter

The examples below assume the config above, whose export is named `phone`, so the client
commands are `j phone ...`. Rename to taste; the CLI follows the export name.

#### Local Exporter

```console
$ jmp shell --exporter-config ./exporter.yaml
$ j phone info
$ exit
```

Useful for bringing a new bench up, since there is nothing else to run.

#### Direct Connection

**On a separate exporter host**, run the exporter as a service and connect to it directly,
with no controller.

On the machine with the device attached:

```shell
jmp run --exporter-config /etc/jumpstarter/exporter.yaml \
  --tls-grpc-listener 0.0.0.0:8083 --tls-grpc-insecure
```

Then from your client device:

```console
$ jmp shell --tls-grpc <exporter-host>:8083 --tls-grpc-insecure -- j phone connect
connected as 127.0.0.1:41000
```

### Attach a device to an existing ADB server

On the Jumpstarter client, run `j <name> connect` to add the ADB device to your local ADB server. For example, to join Android Studio's existing ADB server.

```console
$ j phone connect
connected as 127.0.0.1:41000

Your ADB server now lists it; use it with:  adb -s 127.0.0.1:41000 shell
Android Studio will list it too.

Press Ctrl+C to detach
```

Leave it running for as long as you want the device available. Then, in another
terminal, it is just regular `adb`:

```shell
adb shell
adb install app.apk
adb logcat
adb push local_file.txt /sdcard/
```

Because `adb connect` is **additive**, the device joins whatever your ADB server
already holds — your own emulator, another bench, a phone — and every Android tool
sees it with no configuration. You do not need to own your ADB server, and you do not
need one at all: `adb connect` starts one if none is running.

### Just give me the address

`connect` is a convenience. If you would rather drive adb yourself, or point a tool
that takes a `host:port` at the device:

```console
$ j phone serve
127.0.0.1:41000

Add it to your ADB server with:  adb connect 127.0.0.1:41000
Press Ctrl+C to stop
```

No adb runs on your machine at all. This is the primitive the rest is built on.

### Is my device there?

```console
$ j phone info
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
  run target) needs re-selecting after reconnecting. Use `-P` to pin the port if you
  need one address to stay put.
- Direct mode has no lease arbitration, so two clients connecting the same device will
  interfere. Use distributed mode for a shared fleet.

### Power cycles and re-enumeration

Nothing to configure: the device's serial and its forward are resolved fresh on every
connection. A relay power-cycle re-enumerates USB and can hand the device a different
ADB serial, and the old forward disappears with it — the driver notices, re-resolves
the declared `usb_port` to the new serial, and forwards again. No config edit, no
exporter restart.

Reconnect after the DUT is back up; the local address will generally be a new port.

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

The opposite model to `connect`: instead of adding one device to *your* server, aim
your tools at the exporter's server and see its devices instead of your own. Right for
CI, a headless runner, or a container. Requires a declared `AdbServer`.

```console
$ j adb serve
exporter's ADB server at 127.0.0.1:54321

To use your own adb or other tools, run:
  export ANDROID_ADB_SERVER_ADDRESS=127.0.0.1
  export ANDROID_ADB_SERVER_PORT=54321

Press Ctrl+C to stop
```

This replaces your server rather than adding to it, which is exactly wrong when an IDE
is running — Android Studio owns 5037 and respawns its server there within ~3s of
being killed, so the port cannot reliably be taken over. Use `connect` in that case.

## Integration with Android ecosystem tools

### How this relates to Android's own remote-device support

`connect` is deliberately the same shape as the remote-device flow Google documents, so
Android Studio needs no Jumpstarter-specific support:

- Android's [wireless debugging](https://developer.android.com/tools/adb) has you run
  `adb tcpip 5555` then `adb connect <ip>:5555`, and the device then appears as a
  `host:port` serial alongside your emulators. `connect` does exactly that, except the
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

Run `j phone connect`. The device appears in Studio's device chooser with **no
configuration**: no `adb.server.port`, no environment variables, no restart. Leave the
command running for as long as you want the device available.

### Trade Federation (tradefed)

tradefed discovers devices through the ADB server, so an attached device is visible to
it with no extra setup:

```shell
j phone connect          # leave running
tradefed.sh
# > list devices          <-- shows the attached device
```

To give tradefed the exporter's whole device list instead, use `j adb serve` and
export `ANDROID_ADB_SERVER_PORT`.

### Python API

Drive a device programmatically with [`adbutils`](https://github.com/openatx/adbutils)
against the endpoint, no CLI involved:

```python
# Requires: pip install jumpstarter-driver-adb[python-api]
import adbutils

with client.phone.serve() as target:
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
| `j <device>.adb connect` | Add this device to your own ADB server; holds until Ctrl+C |
| `j <device>.adb serve` | Serve the device's adbd at a local address; holds until Ctrl+C |
| `j <device>.adb info` | Show the device's transport, selector and whether it is present |

Options for `connect` and `serve`:

| Option | Description | Default |
| --- | --- | --- |
| `-H HOST` | Local address to bind | 127.0.0.1 |
| `-P PORT` | Local port to bind (0=auto) | 0 |
| `--adb PATH` | Path to your local adb (`connect` only) | adb |

### Server-level (`j adb ...`, needs a declared `AdbServer`)

| Usage | Description |
| --- | --- |
| `j adb devices` | List devices visible to the exporter's ADB server |
| `j adb serve [-H HOST] [-P PORT]` | Forward the exporter's ADB server to a local port; holds until Ctrl+C |

Everything else is your own `adb`, run directly.

## API Reference

### Device driver

```{eval-rst}
.. autoclass:: jumpstarter_driver_adb.driver.AdbDevice()
    :members: connect, info
```

A parent driver that runs adb itself, rather than streaming the device to a client, can
also use these. They are in-process calls on the child driver object, not exported to
clients — the Cuttlefish driver polls `sys.boot_completed` with its own `adb shell`, and
needs the device in the shared server before any client connects.

```{eval-rst}
.. autoclass:: jumpstarter_driver_adb.driver.AdbDevice
    :members: adb_env, ensure_reachable, disconnect
    :noindex:
```

### Server driver

**Deprecated.** Prefer `AdbDevice`, which exposes one declared device and works with an
ADB server you already run. `AdbServer` is retained for the cases `AdbDevice` cannot
cover — pointing tooling at the exporter's *whole* server, and devices that cannot expose
adbd over TCP — and because the Cuttlefish and Android emulator drivers embed it.

```{eval-rst}
.. autoclass:: jumpstarter_driver_adb.driver.AdbServer()
    :members: list_devices, start_server, kill_server, connect_device, disconnect_device
```

### Device client

```{eval-rst}
.. autoclass:: jumpstarter_driver_adb.client.AdbDeviceClient()
    :members: connect, serve, info
```

### Server client

```{eval-rst}
.. autoclass:: jumpstarter_driver_adb.client.AdbClient()
    :members: forward_adb, devices, list_devices, connect_device, disconnect_device
```
