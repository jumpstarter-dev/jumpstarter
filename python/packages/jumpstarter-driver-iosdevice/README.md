# iOS Device Driver

`jumpstarter-driver-iosdevice` connects existing iOS tools to one leased iPhone
or iPad through usbmux. The exporter owns device pairing; clients use a
lease-scoped proxy identity. Device tools such as go-ios and libimobiledevice
are installed separately.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-iosdevice
```

Choose the tools needed for your workflow. Install these tools separately
under their respective licenses; Jumpstarter does not bundle or download them.

| Tool | Common use |
| --- | --- |
| [go-ios](https://github.com/danielpaulus/go-ios) | Device management and automation |
| [libimobiledevice](https://github.com/libimobiledevice/libimobiledevice) | Device utilities and libraries |
| [usbfluxd](https://github.com/corellium/usbfluxd) | Access a remote usbmux endpoint from local tools |

The exporter needs a running usbmux daemon and a device running iOS 17.4 or
later. Pair the device on the exporter before leasing it. Developer services
also require Developer Mode, a compatible mounted developer image, and any
on-device approvals requested by your tools. Jumpstarter does not configure
these prerequisites or provide physical power control.

### Preparing a physical device

Do this once per device, on the exporter host, with the device unlocked:

1. Connect the device over USB. When iOS asks *Trust This Computer?*, tap
   **Trust** and enter the device passcode. This creates the pairing record the
   exporter owns; clients never receive it.
2. Enable Developer Mode: **Settings > Privacy & Security > Developer Mode**,
   turn it on and restart when asked. After the restart, swipe up to unlock and
   tap **Enable** in the prompt, then confirm with the passcode. If the toggle is
   not shown, connect the device to Xcode or a developer tool once so iOS offers
   it.
3. Verify from the exporter host with libimobiledevice: `idevice_id -l` lists
   the UDID and `idevicepair validate` confirms the pairing.

Keep the device unlocked while tools install or launch test runners; iOS may
ask for further approvals on screen.

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
| `devicehub` | Let macOS clients show the device in Xcode's Device Hub | bool | no | `false` |
| `devicehub_state` | File that keeps the Device Hub identity and paired Macs | str | no | — |
| `devicehub_udid` | UDID the device presents in Device Hub | str | no | the device's UDID |

`http_port` and `devicehub` require exporter trust mode. `http_port` must not
also appear in `forward_ports`. The operator starts the HTTP service. Lockdown port 62078
cannot be configured as `http_port` or forwarded raw in exporter mode.

## Usage

The commands below assume an export named `ios` inside a `jmp shell` session.

```shell
j ios info
j ios serve
j ios -- idevice_id -l
j ios connect
j ios forward 9100
j ios https
j ios devicehub    # macOS clients only
j ios devicehub --wda WebDriverAgentRunner.xctestrun
```

`serve` prints a loopback address and `USBMUXD_SOCKET_ADDRESS` for your tool.
Keep it running while the tool uses the device. The `-- COMMAND...` form sets
that variable for one command. `connect` registers the address with your
installed usbfluxd using `usbfluxctl`, then removes its registration on exit.
An existing registration is left in place.

`forward` accepts only configured device ports. Python's
`forward(port, local_port=0)` selects a free local port; the default local port
matches the device port. Raw forwards retain the application's protocol.

### Device Hub (macOS clients only)

With `devicehub: true` on the exporter, a Mac holding the lease can view and
control the device in Xcode's Device Hub while the device stays plugged into
the exporter. This needs a macOS client with Xcode 27 or later, and Python 3.13
or later on the exporter for the tunnel's TLS-PSK. On other
platforms `devicehub` is left out of `j ios --help`, and running it, or calling
`devicehub()` from Python, fails with an error that says so. On a Mac without
Xcode 27, or before Xcode has been opened once to install its device support,
`devicehub` warns that the device will not appear in Device Hub.

Once per boot, create the virtual network link the Mac uses to reach the
device. The script is installed with this package, in its
`jumpstarter_driver_iosdevice/scripts` directory; `j ios devicehub` prints its
full path when the link is missing:

```shell
sudo sh "$(python3 -c 'from jumpstarter_driver_iosdevice.netstack import SETUP_SCRIPT; print(SETUP_SCRIPT)')"
```

Then, inside a lease:

```shell
j ios devicehub
```

The first time a Mac uses a device, choose File > Pair Nearby Device… in
Device Hub and type the code it shows when the command asks. Later leases show
the device without a code. If the device is not listed in Device Hub, for
example after Unpair, choose File > Pair Nearby Device… while the command runs:
it asks for the new code. `--pair` asks for a code straight away. When the
command stops, the device stays paired and moves to Device Hub's Unavailable
section until the next lease.

Set `devicehub_state` to keep the device's Device Hub identity across exporter
restarts; without it, every Mac pairs again after a restart. Set
`devicehub_udid` only when the same Mac can also see the device directly, as on
a test bench, so that Device Hub keeps the two apart.

### Appium through Device Hub

Device Hub also gives Xcode on the Mac the device's developer services, so
WebDriverAgent (WDA) can run over the same connection: tests run on the device
while you watch them, and can pause to inspect it, in Device Hub.

Build WDA once, signed with a provisioning profile that includes the device;
Jumpstarter does not build or sign it. From the
[WebDriverAgent](https://github.com/appium/WebDriverAgent) checkout that your
Appium XCUITest driver uses:

```shell
xcodebuild build-for-testing -project WebDriverAgent.xcodeproj \
  -scheme WebDriverAgentRunner -destination generic/platform=iOS \
  -derivedDataPath wda DEVELOPMENT_TEAM=<team> \
  PRODUCT_BUNDLE_IDENTIFIER=<your.prefix>.WebDriverAgentRunner
```

Turn on **Settings > Developer > Enable UI Automation** on the device. Then,
inside a lease, pass the `.xctestrun` file the build wrote under
`wda/Build/Products`:

```shell
j ios devicehub --wda wda/Build/Products/WebDriverAgentRunner_iphoneos27.1-arm64.xctestrun
```

The command shows the device in Device Hub, has Xcode install and start WDA
through the lease, and prints WDA's address and the Appium capabilities for it.
WDA stops when the command stops. From Python, `devicehub_wda()` does the same
for the length of a `with` block:

```python
from appium import webdriver
from appium.options.ios import XCUITestOptions

from jumpstarter.common.utils import env

with env() as root, root.ios.devicehub_wda("wda/Build/Products/WebDriverAgentRunner.xctestrun") as wda:
    driver = webdriver.Remote("http://127.0.0.1:4723", options=XCUITestOptions().load_capabilities(wda.capabilities()))
    try:
        driver.execute_script("mobile: activateApp", {"bundleId": "com.apple.Preferences"})
        print(driver.page_source)
    finally:
        driver.quit()
```

Run Appium on the Mac as usual; it reaches WDA at the device's address inside
the Device Hub connection, not over Wi-Fi. Appium finds real devices, reads
their logs and installs apps through usbmux, which this connection does not
provide, so the capabilities skip those steps: start apps with
`mobile: activateApp` rather than `appium:bundleId`, and install them with
`xcrun devicectl device install app` while the device is shown.

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
- **Device Hub:** The Mac pairs with an identity held by the exporter, not with
  the device, and reaches the device through the lease. Like other services,
  the tunnel gives the Mac full access to the device. Pairing a Mac happens
  inside a lease, and a paired Mac sees the device only while it holds a lease
  and runs `j ios devicehub`. The link script makes the user's account able
  to capture packets on every interface until the next reboot.
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
