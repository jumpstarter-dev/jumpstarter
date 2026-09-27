# iOS Simulator Driver

`jumpstarter-driver-iossimulator` manages a simulator on a local macOS exporter.
It provides a `power` child for lifecycle control and an `ios` child for the
shared iOS client, backed by an operator-installed `idb_companion`.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-iossimulator
```

The exporter requires a logged-in macOS session, [Xcode](https://developer.apple.com/xcode/)
command-line tools, an iOS 17.4+ simulator runtime, and
[idb_companion](https://github.com/facebook/idb). Install an idb client on each
macOS or Linux client. External tools are installed separately under their
respective licenses; the driver does not bundle or download them.

For Xcode 27, use a companion containing the upstream display-activity fallback;
released companion 1.6.2 cannot capture screenshots on that runtime. Keep the
companion executable with its distribution's adjacent frameworks and resource
bundles, and set `idb_companion` to its absolute path when needed.

## Configuration

```yaml
export:
  simulator:
    type: jumpstarter_driver_iossimulator.driver.IosSimulator
    config:
      device_type: com.apple.CoreSimulator.SimDeviceType.iPhone-18-Pro
      runtime: com.apple.CoreSimulator.SimRuntime.iOS-27-0
```

Find available identifiers with `xcrun simctl list devicetypes` and
`xcrun simctl list runtimes`. Configure either a new simulator or a golden source:

| Parameter | Description |
| --- | --- |
| `device_type`, `runtime` | Device type and iOS runtime for a new simulator |
| `golden_device_set`, `golden_udid` | Dedicated device set and simulator UUID to clone instead |

A golden source must remain shutdown and unchanged while cloning. The driver
leaves it intact and rejects the user's default CoreSimulator device set as a
source.

| Optional parameter | Default | Description |
| --- | --- | --- |
| `device_name` | `Jumpstarter iOS Simulator` | Simulator name |
| `state_dir` | `/private/tmp` | Existing writable directory for private state; use a short path for Unix sockets |
| `xcrun` | `xcrun` | Xcode tool executable |
| `idb_companion` | `idb_companion` | Companion executable |
| `command_timeout` | `30` | Metadata and creation timeout in seconds |
| `boot_timeout` | `180` | Boot and readiness timeout in seconds |
| `companion_timeout` | `30` | Companion readiness timeout in seconds |
| `shutdown_timeout` | `30` | Shutdown, deletion and process-stop timeout in seconds |
| `http_service` | Unconfigured | HTTPS provider class and options from trusted exporter configuration |

## Usage

Inside a local or leased Jumpstarter shell:

```shell
j simulator power on
j simulator ios info
j simulator ios -- idb describe --json
j simulator ios -- idb install /path/to/YourApp.app
j simulator power off
```

Use `j simulator ios serve` to keep a local companion endpoint open for an
external idb process, or `j simulator ios connect` to register it with idb.

The Python API supports the same transport context managers as physical devices:

```python
from jumpstarter.common.utils import serve
from jumpstarter_driver_iossimulator.driver import IosSimulator

driver = IosSimulator(
    device_type="com.apple.CoreSimulator.SimDeviceType.iPhone-18-Pro",
    runtime="com.apple.CoreSimulator.SimRuntime.iOS-27-0",
)
with serve(driver) as simulator:
    simulator.power.on()
    with simulator.ios.serve() as endpoint:
        print(endpoint)  # Pass to idb's --companion option.
    simulator.power.off(destroy=True)
```

`power.on()` is idempotent. `power.off()` stops the companion and simulator while
preserving the lease's data; another `on()` reuses it. A failed first `on()`
removes the partially created simulator; a failed restart only stops it, keeping
the lease's data for a retry. `off(destroy=True)`, reset and session close delete
the owned device set. `power.read()` is unsupported.

### Optional HTTPS service

A trusted exporter configuration can select a service provider. Clients use the
generic `ios.https()` context manager or `j simulator ios https -- COMMAND`;
they cannot select the provider, executable, host address or provider options.

The included `AppiumProvider` requires an external
[Appium](https://appium.io/docs/en/latest/) installation, its
[XCUITest extension](https://github.com/appium/appium-xcuitest-driver), Node.js 22
first on the exporter's `PATH`, and a complete prebuilt simulator
[WebDriverAgent app](https://github.com/appium/WebDriverAgent). Appium serves the
provider's private backend over HTTPS, which fails on Node.js 24 and later
(`No such module: http_parser`) even though Appium itself supports them; the
provider reports this with the detected Node.js version. Add this to the
simulator's `config`:

```yaml
http_service:
  provider: jumpstarter_driver_iossimulator.appium.AppiumProvider
  options:
    executable: /opt/appium/node_modules/.bin/appium
    home: /opt/appium/extensions
    wda: /opt/appium/WebDriverAgentRunner-Runner.app
    startup_timeout: 60
    request_timeout: 180
```

The service starts when HTTPS metadata is requested after `power.on()`. Install
the application through idb, then create a native WebDriver session using its
bundle ID. The provider fixes the simulator, private device set, runner and
service ports. It permits one active session and one W3C `firstMatch` choice.
[Appium-Python-Client](https://github.com/appium/python-client) works unchanged
when given the endpoint's CA:

```python
from appium import webdriver
from appium.options.ios import XCUITestOptions
from appium.webdriver.client_config import AppiumClientConfig

with simulator.ios.https() as endpoint:
    options = XCUITestOptions()
    options.bundle_id = "com.example.app"
    config = AppiumClientConfig(remote_server_addr=endpoint.url, ca_certs=str(endpoint.ca_file))
    driver = webdriver.Remote(endpoint.url, options=options, client_config=config)
    try:
        print(driver.page_source)
    finally:
        driver.quit()
```

```python
with simulator.ios.https() as endpoint:
    url = endpoint.url
    context = endpoint.ssl_context()
    device = endpoint.metadata["device"]
```

The endpoint exposes its URL, public CA file and provider metadata. The provider
returns `{"protocol": "webdriver", "device": {"platform": "iOS", "udid": ..., "version": ...}}`.
Use this metadata with your automation client and retain certificate and hostname
verification. The CLI passes `JUMPSTARTER_IOS_HTTPS_URL`, `JUMPSTARTER_IOS_HTTPS_CA_FILE`
and `JUMPSTARTER_IOS_HTTPS_METADATA` to the command. The client API is shared
with the {doc}`iOS device driver <iosdevice>`.

## Security and limitations

Each lease owns a private device set and companion Unix socket. Lifecycle
commands explicitly select that set; failures retain ownership when cleanup
must be retried. Core lifecycle uses `simctl` without launching Simulator.app or
Device Hub. Only local macOS execution is supported.

The owning exporter holds a lock on its lease directory. If an exporter exits
without cleanup, for example after a crash, the next lease or session reset in
the same `state_dir` shuts down and deletes that lease's simulators, stops
processes still using its directory, such as its companion and Appium, and
removes it. Live leases and directories without a lease lock are never touched.

Simulators share the Mac's network namespace, so arbitrary TCP forwarding is
unavailable. A configured HTTP provider is trusted exporter code and must
restrict access to its simulator. Its constructor must clean partial resources
on failure; its idempotent `close()` must revoke streams and remove owned state.

`AppiumProvider` accepts native source, element/input, screenshot, alert,
orientation and bounded action commands, plus a limited set of app lifecycle,
keyboard and gesture commands, including those used by Appium-Python-Client's
app, keyboard and `background_app` helpers. Client capabilities are limited to `bundleId`, launch,
termination and alert booleans, `newCommandTimeout` (1–600 seconds), and
`waitForIdleTimeout` (0–30 seconds). Host paths and URLs, installation, process
arguments/environment, file transfer, arbitrary scripts, plugins and web
contexts are rejected. Install applications through idb.

The provider verifies its private Appium backend over HTTPS. WDA HTTP is bound
to loopback; exclusive IPv4/IPv6 port reservations disable its optional wildcard
MJPEG broadcaster. Standard screenshots remain available. No private HTTPS key
is sent to clients. Power off, reset and lease teardown revoke HTTPS streams and
stop the service before simulator shutdown. Errors keep their standard W3C code,
so clients raise their usual exceptions, while messages and stack traces stay in
the exporter log. A session that Appium refuses, for example for an uninstalled
bundle ID, returns `session not created` and leaves the service usable. An
uncertain session creation or deletion, such as a timeout, invalidates the
service and requires a power cycle; the failing request still receives its error.

Appium uses a private `HOME` but may open Device Hub. The provider fixes
`isHeadless=false` to prevent Appium from stopping an existing UI process.
Use a dedicated exporter for automation; concurrent GUI workflows are outside
this isolation boundary.

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_iossimulator.driver.IosSimulator()

.. autoclass:: jumpstarter_driver_iosdevice.client.IosDeviceClient()
   :members: info, serve, connect, https
   :no-index:

.. autoclass:: jumpstarter_driver_iossimulator.http_service.HttpServiceProvider()
   :members:
```
