# iOS Device Driver

`jumpstarter-driver-iosdevice` connects existing iOS tools to one leased iPhone
or iPad through usbmux. The exporter owns device pairing; clients use a
lease-scoped proxy identity. Device tools such as pymobiledevice3, go-ios,
libimobiledevice and Appium are installed separately.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-iosdevice
```

Choose the tools needed for your workflow. Install these tools separately
under their respective licenses; Jumpstarter does not bundle or download them.

| Tool | Common use |
| --- | --- |
| [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) | Device management and developer services |
| [go-ios](https://github.com/danielpaulus/go-ios) | Device management and automation |
| [libimobiledevice](https://github.com/libimobiledevice/libimobiledevice) | Device utilities and libraries |
| [usbfluxd](https://github.com/corellium/usbfluxd) | Access a remote usbmux endpoint from local tools |
| [Appium](https://appium.io/docs/en/latest/) | WebDriver automation with XCUITest and WDA |

The exporter needs a running usbmux daemon and a device running iOS 17.4 or
later. Pair the device on the exporter before leasing it. Developer services
also require Developer Mode, a compatible mounted developer image, and any
on-device approvals requested by your tools. Jumpstarter does not configure
these prerequisites or provide physical power control.

## Configuration

```yaml
export:
  ios:
    type: jumpstarter_driver_iosdevice.driver.IosDevice
    config:
      usb_port: "1-4.2"
      http_port: 8100
      forward_ports: [9100]
```

On Linux, `usb_port` is the device's USB topology name under
`/sys/bus/usb/devices`. It selects the bench port, so replacing the device does
not require changing the configuration. On macOS, or to select a particular
device, use `udid` instead. USB transport requires exactly one selector.

For an operator-provided remote usbmux daemon, set `transport: network`,
`usbmux_host` and `udid`. The endpoint must speak usbmux; it is not the device's
own IP address. Protect the network connection to the remote daemon separately;
that connection uses plain TCP.

### Config parameters

| Parameter | Description | Type | Required | Default |
| --- | --- | --- | --- | --- |
| `transport` | Local USB daemon or remote usbmux daemon: `usb` or `network` | str | no | `usb` |
| `usb_port` | Linux USB topology name, for example `1-4.2` | str | one USB selector | — |
| `udid` | Device identifier | str | one USB selector; required for network | — |
| `usbmux_socket` | Local usbmux daemon socket | str | no | `/var/run/usbmuxd` |
| `usbmux_host` | Remote usbmux daemon host | str | for network | — |
| `usbmux_port` | Remote usbmux daemon port | int | no | `27015` |
| `trust_mode` | `exporter` or explicit credential-sharing `passthrough` | str | no | `exporter` |
| `forward_ports` | Device ports available as raw TCP forwards | list[int] | no | `[]` |
| `http_port` | Device HTTP service to expose through verified HTTPS | int | no | — |
| `connect_timeout` | Discovery and connection timeout, in seconds | float | no | `10.0` |
| `kind` | Target label: `physical` or `corellium`; does not provision devices | str | no | `physical` |

`http_port` requires exporter trust mode and must not also appear in
`forward_ports`. The operator starts the HTTP service. Lockdown port 62078
cannot be configured as `http_port` or forwarded raw in exporter mode.

## Usage

The commands below assume an export named `ios` inside a `jmp shell` session.

```shell
j ios info
j ios serve
j ios -- pymobiledevice3 usbmux list
j ios connect
j ios forward 9100
j ios https
```

`serve` prints a loopback address and `USBMUXD_SOCKET_ADDRESS` for your tool.
Keep it running while the tool uses the device. The `-- COMMAND...` form sets
that variable for one command. `connect` registers the address with your
installed usbfluxd using `usbfluxctl`, then removes its registration on exit.
An existing registration is left in place.

`forward` accepts only configured device ports. Python's
`forward(port, local_port=0)` selects a free local port; the default local port
matches the device port. Raw forwards retain the application's protocol.

### HTTPS services

`https(port=0)` returns an endpoint with `url`, `ca_file` and provider-defined
JSON `metadata`. It does not interpret metadata or configure a particular tool.
For example, an HTTP service with a `/status` route can be queried with:

```python
import urllib.request

from jumpstarter.common.utils import env

with env() as root, root.ios.https() as endpoint:
    with urllib.request.urlopen(endpoint.url + "/status", context=endpoint.ssl_context(), timeout=10) as response:
        print(response.read().decode())
```

`j ios https` prints those three fields as JSON and waits until Ctrl+C.
`j ios https -- COMMAND...` supplies `JUMPSTARTER_IOS_HTTPS_URL`,
`JUMPSTARTER_IOS_HTTPS_CA_FILE`, and JSON `JUMPSTARTER_IOS_HTTPS_METADATA` to the command.

`endpoint.environment()` copies the caller's environment and adds Node CA
trust through `NODE_EXTRA_CA_CERTS`. Existing explicit CA bundles are extended
without modifying their files. Unset Python trust variables remain unset;
Python callers use `ssl_context()` or their HTTP library's CA-file option.
Certificate and hostname verification stay enabled, and
`NODE_TLS_REJECT_UNAUTHORIZED=0` is rejected.

All listeners bind `127.0.0.1`. Closing a context removes its listener and
public CA files; keep the context open for the tool's entire lifetime. Exporter
services and identities are revoked at lease teardown, reset or device detach.
Externally started device services remain owned by their launcher.

### Appium

[Appium](https://appium.io/docs/en/latest/) with its
[XCUITest driver](https://github.com/appium/appium-xcuitest-driver) reaches an
operator-started [WebDriverAgent (WDA)](https://github.com/appium/WebDriverAgent)
through `https()`. Set `http_port` to WDA's port, usually 8100, and do not also
forward it. Jumpstarter does not install, sign or start WDA: pair the device on
the exporter, enable Developer Mode, mount a developer image, install a signed
WDA runner and your application, and start WDA with your device tools. Keep the
device unlocked and approve testing when iOS asks.

Run Appium on the client with the endpoint's trust environment and the lease's
usbmux address, then connect a WebDriver client such as
[Appium-Python-Client](https://github.com/appium/python-client):

```python
import subprocess
import time
import urllib.request

from appium import webdriver
from appium.options.ios import XCUITestOptions

from jumpstarter.common.utils import env

with env() as root, root.ios.serve() as usbmux, root.ios.https() as endpoint:
    tool_env = endpoint.environment()
    tool_env["USBMUXD_SOCKET_ADDRESS"] = usbmux
    appium = subprocess.Popen(["appium", "server", "--address", "127.0.0.1", "--port", "4723"], env=tool_env)
    try:
        while True:
            try:
                urllib.request.urlopen("http://127.0.0.1:4723/status", timeout=5)
                break
            except OSError:
                time.sleep(1)
        options = XCUITestOptions()
        options.udid = endpoint.metadata["device"]["udid"]
        options.bundle_id = "your.installed.app"
        options.no_reset = True
        options.set_capability("appium:webDriverAgentUrl", endpoint.url)
        driver = webdriver.Remote("http://127.0.0.1:4723", options=options)
        try:
            print(driver.page_source)
        finally:
            driver.quit()
    finally:
        appium.terminate()
        appium.wait()
```

Appium trusts WDA's certificate through the `NODE_EXTRA_CA_CERTS` value from
`environment()`. Keep both contexts open until Appium stops, and stop the
Appium and WDA processes you started before releasing the lease. For
simulators, the exporter runs Appium itself; see {doc}`iossimulator`.

## Security and limits

- **Exporter trust:** The default mode keeps the real pairing identity on the
  exporter. Tools receive disposable credentials and establish TLS to the
  exporter through Jumpstarter. The exporter uses a separate TLS connection to
  the device and pins its certificate to the pairing record. Service streams
  are encrypted to the exporter even when the device service uses plaintext.
- **Bootstrap trust:** Proxy credentials and HTTPS CAs arrive through
  Jumpstarter's authenticated RPC channel. Their authenticity depends on that
  deployment; they do not independently protect against infrastructure that
  can replace the bootstrap metadata. Native TLS terminates at the trusted
  exporter, not at the device.
- **Device isolation:** Discovery, pair-record reads and connections are scoped
  to the selected device on its USB link; a Wi-Fi copy of the same device is
  ignored. Pair-record writes are rejected; exporter mode also rejects client
  pairing changes.
- **Lease access:** A lease grants full control of the device. The policy above
  protects the exporter's pairing identity, not device capabilities: services
  started through lockdown, including the iOS 17.4+ CoreDevice tunnel, reach
  every service and TCP port on the device without further filtering.
  `forward_ports` selects convenience forwards; it is not an access boundary.
  Reset or re-provision devices between untrusted tenants.
- **Passthrough:** `trust_mode: passthrough` exposes the selected device's real
  pairing record for tools that require direct device TLS. Use it only when
  credential sharing is acceptable. Opaque lockdown traffic cannot receive the
  exporter's pairing-change policy, and `http_port` is unavailable in this mode.
- **HTTP and raw ports:** HTTPS for `http_port` ends on the exporter; its final
  USB connection uses HTTP. Raw `forward_ports` do not gain native TLS.
- **Service compatibility:** Services that switch from TLS to plaintext after
  authentication, including legacy debugserver, instruments, accessibility and
  testmanager services, are rejected in exporter mode. The driver does not
  implement device actions or guarantee every external tool's service support.
- **Lifetime:** Reconnect after device detach. Pending service handles expire
  after 60 seconds and are single-use; active connections and pending handles
  are limited to 128 each. Disposable certificates expire after seven days;
  obtain a fresh lease for longer use.

Local simulators use the separate
{doc}`jumpstarter-driver-iossimulator package <iossimulator>`.

## API Reference

Driver:

```{eval-rst}
.. autoclass:: jumpstarter_driver_iosdevice.driver.IosDevice()
```

Client:

```{eval-rst}
.. autoclass:: jumpstarter_driver_iosdevice.client.IosDeviceClient()
    :members: info, serve, connect, forward, https, run
```

HTTPS endpoint:

```{eval-rst}
.. autoclass:: jumpstarter_driver_iosdevice.client.HttpsEndpoint()
    :members: environment, ssl_context
```
