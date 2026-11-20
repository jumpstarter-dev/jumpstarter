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

To see the device and control it through an HMI (see [HMI (scrios)](#hmi-scrios))
install the `hmi` extra, which adds the {doc}`HMI interface <hmi>`:

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} "jumpstarter-driver-iosdevice[hmi]"
```

Choose the tools needed for your workflow. Install these tools separately
under their respective licenses; Jumpstarter does not bundle or download them.

| Tool | Common use |
| --- | --- |
| [go-ios](https://github.com/danielpaulus/go-ios) | Device management and automation |
| [libimobiledevice](https://github.com/libimobiledevice/libimobiledevice) | Device utilities and libraries |
| [usbfluxd](https://github.com/corellium/usbfluxd) | Access a remote usbmux endpoint from local tools |
| [Appium](https://appium.io/docs/en/latest/) | UI automation; the exporter can run it for the lease |
| [scrios](https://github.com/kirkbrauer/scrios) | The device's screen as H.264 or HEVC, with touch, keyboard keys, buttons and typing; the exporter can run it for the lease |

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
3. For UI automation with Appium, enable **Settings > Developer > Enable UI
   Automation**.
4. Verify from the exporter host with libimobiledevice: `idevice_id -l` lists
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
| `appium` | Run Appium on the exporter for this device; see [Appium](#appium) | dict | no | — |
| `hmi` | Serve the device as an HMI with scrios on the exporter; see [HMI (scrios)](#hmi-scrios) | dict | no | — |

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

The Mac reaches the device through a virtual network link, created once per
boot with administrator rights. When it is missing, `j ios devicehub` explains
what it changes and offers to create it; `sudo` then asks for your password.
The link lets your account capture packets on every interface until the next
reboot. Without a terminal to answer in, the command prints the setup command
instead; the script is installed with this package, in its
`jumpstarter_driver_iosdevice/scripts` directory.

Inside a lease:

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
while you watch them, and can pause to inspect it, in Device Hub. Appium then
runs on the Mac rather than the exporter; the
{doc}`UI automation guide <../../../getting-started/guides/ui-automation>`
compares the two.

Build WDA once, signed with a provisioning profile that includes the device;
Jumpstarter does not build or sign it. From the
[WebDriverAgent](https://github.com/appium/appium-ios/tree/main/packages/webdriveragent) checkout that your
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
through the lease, starts your installed Appium on this Mac, and prints the
Appium URL, WDA's address and the capabilities to use. `--appium PATH` selects
the Appium launcher, and `-- COMMAND` runs a command with the endpoint in its
environment instead. Appium and WDA stop when the command stops. From Python,
`devicehub_appium()` does the same for the length of a `with` block and yields
the same target as `ios.appium.serve()`, so tests do not change between the two:

```python
from appium import webdriver
from appium.options.ios import XCUITestOptions

from jumpstarter.common.utils import env

with env() as root, root.ios.devicehub_appium("wda/Build/Products/WebDriverAgentRunner.xctestrun") as target:
    options = XCUITestOptions().load_capabilities(target.capabilities())
    options.set_capability("appium:bundleId", "com.apple.Preferences")
    # or an application of your own: options.set_capability("appium:app", target.upload_app("build/MyApp.ipa"))
    driver = webdriver.Remote(target.url, options=options, client_config=target.client_config())
    try:
        print(driver.page_source)
    finally:
        driver.quit()
```

Appium runs on the Mac here because only the Mac reaches WDA, at the device's
address inside the Device Hub connection rather than over Wi-Fi. Install
Appium and its XCUITest driver on the Mac; Jumpstarter does not bundle them.

Sessions behave as on the exporter's Appium. Appium installs apps, reads logs
and resets devices through usbmux, which this connection does not provide, so
the target's URL is a gateway in front of it that applies the exporter's rules
and fills the gap:

- `appium:app`, `appium:otherApps` and `mobile: installApp` take tokens from
  `target.upload_app()` (an `.ipa`, a zipped `.app` or an `.app` bundle built
  for devices). The gateway installs them with `xcrun devicectl`, removing an
  installed copy first unless you ask for `appium:noReset`, as Appium does,
  and starts `appium:app` by its bundle identifier. Tokens from
  `ios.appium.upload_app()` belong to the exporter's Appium and do not work
  here.
- `appium:bundleId` works: Appium checks for installed apps through devicectl.
- `mobile: removeApp` uninstalls with devicectl, and `mobile: killApp` stops
  the app as `mobile: terminateApp` does.
- `appium:fullReset`, `appium:usePreinstalledWDA` and `appium:mjpegServerPort`
  are ignored, as on the simulator, where the target fixes them;
  `appium:resetLocationService` and `appium:customSSLCert` are rejected.
- Web views, MJPEG screen streaming, `mobile: listApps` and the device's logs
  also need usbmux and are not available.

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

With an `appium` block, the exporter runs [Appium](https://appium.io/docs/en/latest/)
for the lease, and clients open sessions through the shared
{doc}`Appium client <appium>`, the same interface every UI-automation target
provides. The
{doc}`UI automation guide <../../../getting-started/guides/ui-automation>`
explains why Jumpstarter uses Appium and how to set it up.
The exporter needs Appium and its
[XCUITest driver](https://github.com/appium/appium-xcuitest-driver), usually
pinned in the exporter's container image; clients do not need Appium.
Jumpstarter does not build, sign, install or bundle
[WebDriverAgent (WDA)](https://github.com/appium/appium-ios/tree/main/packages/webdriveragent).

Appium reaches the device through the exporter's own usbmux daemon (the
`usbmux_socket`, or `usbmux_host` for network transport) and every session is
pinned to the device's udid and iOS version. Choose how WDA starts:

- **Attach to a running WDA** with `wda_port`, usually 8100. Start WDA with
  your device tools; Appium reaches it through a loopback forward on the
  exporter.

  ```yaml
  appium:
    executable: /opt/appium/node_modules/.bin/appium
    home: /opt/appium/home
    wda_port: 8100
  ```

- **Let Appium launch an installed WDA** by fixing the capabilities. On iOS 17
  and later Appium launches it through a RemoteXPC tunnel, which the exporter
  host must allow; see the XCUITest driver's documentation.

  ```yaml
  appium:
    capabilities:
      appium:usePreinstalledWDA: true
      appium:updatedWDABundleId: dev.example.WebDriverAgentRunner
  ```

`capabilities`, `env`, `executable`, `home`, `startup_timeout` and
`max_upload_bytes` are described in {doc}`appium`. Then, inside a lease:

```python
from appium import webdriver
from appium.options.ios import XCUITestOptions

from jumpstarter.common.utils import env

with env() as root, root.ios.appium.serve() as target:
    options = XCUITestOptions().load_capabilities(target.capabilities())
    options.bundle_id = "com.apple.Preferences"
    driver = webdriver.Remote(target.url, options=options, client_config=target.client_config())
    try:
        print(driver.page_source)
    finally:
        driver.quit()
```

`j ios appium` prints the endpoint, `j ios appium -- COMMAND` runs a command
with it in the environment, and `j ios appium upload APP.ipa` uploads an app
for `appium:app`. Appium sees every device on the exporter's usbmux daemon but
sessions only reach the leased one; give each device its own exporter or
usbmux endpoint when tenants must not see each other's devices.

To run Appium on the client instead, configure `http_port` for an
operator-started WDA and use [HTTPS services](#https-services) for its URL
together with `j ios serve` for usbmux. On a Mac,
[Appium through Device Hub](#appium-through-device-hub) starts a WDA build you
provide.

### HMI (scrios)

With a `hmi` block the exporter serves the device as an HMI, through the
{doc}`HMI interface <hmi>`: its screen as H.264 (or HEVC) and the input the
device takes back, which here is touch, keyboard keys, the device's buttons and
typed text. One stream serves any number of viewers; there is a recording, and
the same client and web view (`j ios hmi view`) as every other HMI. It needs
the `hmi` extra (`pip install jumpstarter-driver-iosdevice[hmi]`); a device
without the option never loads it.

```yaml
export:
  ios:
    type: jumpstarter_driver_iosdevice.driver.IosDevice
    config:
      usb_port: "1-4.2"
      hmi: {}                  # or: {executable: /opt/scrios/scrios, codec: hevc, control: false}
```

The picture comes from [scrios](https://github.com/kirkbrauer/scrios), a
separate program (Apache-2.0) that reads the device's CoreDevice display
service through the exporter's own usbmux daemon, with the pairing the exporter
already holds, and writes H.264 and events on its standard output. The driver
runs it as `scrios stream --format framed --udid UDID --video-codec h264`
(`h265` for HEVC), adding `--usbmuxd` (the device driver's own address),
`--no-control` for a view-only HMI, and what the options below ask for. Nothing
of scrios reaches the client: touch, keys, buttons and text from any viewer are
turned into scrios's commands on the exporter.

**Where the binary comes from.** Install a release of scrios on the exporter:
`scrios-x86_64-linux-musl` or `scrios-aarch64-linux-musl` from
[its releases](https://github.com/kirkbrauer/scrios/releases), with the
`SHA256SUMS` next to it. They are static binaries for any Linux distribution.
Name the file with `executable:` or put it on `PATH` as `scrios`; a missing
binary is a start error that says so. The repository's container image
downloads a pinned release at build time (the `SCRIOS_VERSION` and
`SCRIOS_BASE_URL` build arguments; point the latter at a mirror of the release
assets if you keep one), checks it against `SHA256SUMS`, and installs it as
`/usr/local/bin/scrios`. Nothing is downloaded at run time. The driver asks the
binary for `--version` once before using it, and the first `config` it writes
must say `protocol: 1` of its framed protocol, so a scrios that is too old or
too new for this driver is refused with a message that says so.

What the device needs is what scrios needs (Developer Mode, the developer disk
image, a trusted pairing, iOS 27 or later for the display service): see its
documentation, and run `scrios doctor` on the exporter. Run it before the first
lease; the driver starts scrios in its framed mode, which waits for nothing and
reports what is missing as an error.

Options of `hmi` (besides the common ones of the
{doc}`HMI interface <hmi>`, such as `control`, `hard_keys`, `idle_stop_s`
and `stop_deadline_s`):

| Parameter | Description | Type | Default |
| --- | --- | --- | --- |
| `executable` | The scrios binary: a path, or a name looked up on `PATH` | str | `scrios` |
| `extra_args` | More options for `scrios stream`, for example `["--rtp-stall-timeout-ms", "20000"]`; the ones the driver sets itself are refused | list[str] | `[]` |
| `udid` | The device; by default the device driver's own (or what its `usb_port` holds) | str | — |
| `usbmuxd` | The usbmux daemon scrios connects through, `UNIX:/path` or `host:port`; by default the device driver's own | str | — |
| `display_id` | The display to stream (`--display-id`) | int | scrios's own, 1 |
| `visible_size` | The picture's visible size, `1206x2622` (`--visible-size`) | str | what the device says |
| `hid_space`, `hid_swap_xy`, `hid_flip_x`, `hid_flip_y` | How a touch is mapped onto the digitizer (`--hid-space auto\|panel\|ui` and the three flags). scrios works the mapping out itself; these are overrides, only with `control` | str, bool | scrios's own |
| `stop_grace_s` | After `stop` on its stdin scrios is given this long to leave before SIGTERM, then the same again before SIGKILL, all within `stop_deadline_s` | float | `2.0` |
| `restart_backoff_s`, `restart_backoff_max_s` | After a second failure in a row the next start waits this long, doubling up to the maximum | float | `1.0`, `30.0` |
| `start_timeout_s` | How long scrios has to say what the picture is (it connects through the device's tunnel) | float | `30.0` |
| `stall_s` | Silence, with a viewer attached, after which scrios is asked for a key picture, and after as long again restarted (once) | float | `8.0` |

`codec` of the common options is `h264` (the default) or `hevc`; `mjpeg` is a
`ConfigurationError`, and so are `max_size`, `bit_rate` and `max_fps`: scrios
streams the display at its own size, rate and bit rate, so the quality is not
adjustable (`set_quality` says so) and the stream is limited only by the network
between the client and the exporter. A clean stop of scrios can take up
to about 8 seconds; the defaults end it sooner, and an operator who prefers it
to finish can raise `stop_grace_s` and `stop_deadline_s`.

**Codecs.** The HMI declares `h264` and `hevc` in `hmi_info()`. H.264 is the
default; choose HEVC with `codec: hevc`, or per lease with `start(codec="hevc")`
(the stream restarts with it). The codec line names what scrios's `config`
event says (`avc1.*`, `hev1.*` or `hvc1.*`), and a scrios that streams the other
codec than the one asked for, or any other codec, is a start error that says
so. HEVC needs a viewer that can decode it.

**Input.** The HMI declares exactly what scrios supports, and the exporter
passes nothing else on:

- *Touch*: one finger (id 0), at pixels of the picture the viewer is shown
  (upright, after the rotation the codec line reports); scrios maps it onto
  the digitizer. No mouse, pen or wheel.
- *Keyboard keys*: the keys of a full-size keyboard, by their
  `KeyboardEvent.code`: letters, digits, the punctuation keys, `Enter`,
  `Escape`, `Backspace`, `Tab`, `Space`, `CapsLock`, `F1` to `F12`, `Insert`,
  `Home`, `PageUp`, `Delete`, `End`, `PageDown`, the four arrows, the numeric
  keypad and the eight modifiers (`ShiftLeft`, `ControlRight`, `MetaLeft`, ...).
  `hmi_info()["input"]["keyboard"]["codes"]` lists them. Each goes to scrios as
  `kb down USAGE` and `kb up USAGE` with the key's USB HID usage (`KeyA` is
  `04`, `Enter` is `28`), so it is the physical key that is pressed and the
  phone's keyboard layout decides which character it makes. A key stays down
  until it is let go; the exporter lets go of the keys a client leaves down when
  its input stream ends, and scrios itself does 30 seconds after the last
  keyboard command and when it stops.
- *Typed text*: typed by scrios through a US layout (`layout: us`): letters,
  digits, the printable ASCII punctuation, space, newline and tab; anything else
  (accented letters, emoji, `\r`) is skipped and logged.
- *Buttons*: as the `hmi_info()` of the HMI lists them: `HOME`, `APP_SWITCH`
  (the Home button pressed twice, which opens the App Switcher), `VOLUME_UP`,
  `VOLUME_DOWN`, `MUTE` and `ASSIST` (Siri); `short` and `long` presses only,
  since scrios holds a button for its hold time itself. `LOCK` and `POWER` (the
  same button) are **opt-in**: pressing it can lock the user out of a device
  whose only way in is its screen, so the HMI accepts it only when `hard_keys`
  lists it (`hard_keys: [HOME, APP_SWITCH, LOCK]`). There is no back button on
  an iPhone.

Touch and buttons are answered with an error line (with scrios's reason) until
scrios says that the device's touch and key services are ready, and keys and
text until it also says that its keyboard works. A key that is already down when
the keyboard stops working can still be let go.

**Limits.** H.264 and HEVC only (no MJPEG), one finger, no mouse, pen, wheel or
clipboard. Control of the
device needs the display service's touch and key services; when they cannot be
connected scrios runs without them and `status()["device"]["hid"]` says why.
The status also has scrios's own facts: its version, process and uptime, the
codec, the statistics it reports, what it last said and how it last ended, and
the last lines of its log. The driver's reading of scrios's output, its input
queue and the way it stops are bounded: a scrios that stops reading its standard
input, or that ignores `stop` and SIGTERM, cannot hold up the stream, the
exporter or the end of a lease.

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
    :members: info, serve, connect, forward, https, run, devicehub, devicehub_appium, appium, hmi
```

HMI (needs the `hmi` extra):

```{eval-rst}
.. autoclass:: jumpstarter_driver_iosdevice.hmi.IosDeviceHmi()
```

HTTPS endpoint:

```{eval-rst}
.. autoclass:: jumpstarter_driver_iosdevice.client.HttpsEndpoint()
    :members: environment, ssl_context
```
