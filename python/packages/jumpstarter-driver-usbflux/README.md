# usbflux Driver

`jumpstarter-driver-usbflux` exposes one device on a usbmux daemon through a
lease. Tools see only that device; they may read its pairing record and the
daemon's host identity, and cannot change pairing or other daemon state. The
client serves the endpoint on loopback, registers it with a local
[usbfluxd](https://github.com/corellium/usbfluxd), or runs a tool with
`USBMUXD_SOCKET_ADDRESS` set.

The daemon's real pairing record is handed to tools. For exporter-owned pairing
credentials, lockdown trust brokering, HTTPS services and raw port forwards, use
{doc}`jumpstarter-driver-iosdevice <iosdevice>`, which builds on this package.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-usbflux
```

The exporter needs a running usbmux daemon: usbmuxd on Linux or the built-in
daemon on macOS. Install device tools such as go-ios or libimobiledevice
separately under their respective licenses.

## Configuration

```yaml
export:
  usbmux:
    type: jumpstarter_driver_usbflux.driver.UsbFlux
    config:
      udid: "00008030-000000000000002E"
```

For an operator-provided remote usbmux daemon, set `transport: network` and
`usbmux_host`. The endpoint must speak usbmux; it is not the device's own IP
address. Protect that connection separately; it uses plain TCP.

### Config parameters

| Parameter | Description | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `udid` | Device identifier as listed by the daemon | str | yes | — |
| `transport` | Local daemon socket or remote daemon: `usb` or `network` | str | no | `usb` |
| `usbmux_socket` | Local usbmux daemon socket | str | no | `/var/run/usbmuxd` |
| `usbmux_host` | Remote usbmux daemon host | str | for network | — |
| `usbmux_port` | Remote usbmux daemon port | int | no | `27015` |
| `connect_timeout` | Discovery and connection timeout, in seconds | float | no | `10.0` |

With `usb` transport only the device's USB link is visible; a Wi-Fi copy of the
same device is ignored.

## Usage

The commands below assume an export named `usbmux` inside a `jmp shell` session.

```shell
j usbmux info
j usbmux serve
j usbmux -- idevice_id -l
j usbmux connect
```

`serve` prints a loopback address and `USBMUXD_SOCKET_ADDRESS` for your tool.
Keep it running while the tool uses the device. The `-- COMMAND...` form sets
that variable for one command. `connect` registers the address with your
installed usbfluxd using `usbfluxctl`, then removes its registration on exit.
An existing registration is left in place.

```python
from jumpstarter.common.utils import env

with env() as root, root.usbmux.serve() as address:
    print(address)  # 127.0.0.1:PORT
```

### usbfluxd adapter

`UsbfluxAdapter` is the reusable form of `connect`: it serves any driver's
usbmux stream on loopback and registers it with usbfluxd for the lifetime of
the context.

```python
from jumpstarter_driver_usbflux.adapters import UsbfluxAdapter

from jumpstarter.common.utils import env

with env() as root, UsbfluxAdapter(client=root.usbmux) as target:
    print(f"usbfluxd now lists the leased device via {target}")
```

Both `serve(port=...)` and the adapter accept a fixed local port; a fixed port
is never shared with another listener.

## Security and limits

- Discovery, pair-record reads and connections are scoped to the selected
  device. Pair-record writes and other daemon state changes are rejected.
- Connected device ports carry whatever the device service speaks; this
  driver adds no encryption of its own.
- A lease grants full control of the device through its daemon. Reset or
  re-provision devices between untrusted tenants.

## API Reference

Driver:

```{eval-rst}
.. autoclass:: jumpstarter_driver_usbflux.driver.UsbFlux()
```

Client:

```{eval-rst}
.. autoclass:: jumpstarter_driver_usbflux.client.UsbFluxClient()
    :members: info, serve, connect, run
```

Adapter:

```{eval-rst}
.. autofunction:: jumpstarter_driver_usbflux.adapters.UsbfluxAdapter
```
