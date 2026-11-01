# iOS Simulator Driver

`jumpstarter-driver-iossimulator` manages a simulator on a local macOS exporter.
It provides a `power` child for lifecycle control, an `ios` child for the
shared iOS client, backed by an operator-installed `idb_companion`, and an
optional `appium` child for UI automation.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-iossimulator
```

The exporter requires a logged-in macOS session, the full
[Xcode](https://developer.apple.com/xcode/) app selected as the active developer
directory (`sudo xcode-select -s /Applications/Xcode.app`; the standalone
command-line tools lack `simctl`), an iOS 17.4+ simulator runtime, and
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

### Appium

With an `appium` block in the simulator's `config`, the exporter runs
[Appium](https://appium.io/docs/en/latest/) for the lease and clients open
sessions through the shared {doc}`Appium client <appium>`, the same interface
every UI-automation target provides. The
{doc}`UI automation guide <../../../getting-started/guides/ui-automation>`
explains why Jumpstarter uses Appium and how to set it up. The exporter needs
Appium, its [XCUITest driver](https://github.com/appium/appium-xcuitest-driver)
and a complete prebuilt simulator
[WebDriverAgent app](https://github.com/appium/WebDriverAgent); Jumpstarter does
not install or build them. Clients do not need Appium.

```yaml
appium:
  executable: /opt/appium/node_modules/.bin/appium
  home: /opt/appium/home
  wda: /opt/appium/WebDriverAgentRunner-Runner.app
```

| Option | Description |
| --- | --- |
| `wda` | Prebuilt simulator WebDriverAgent `.app` (required) |
| `executable` | Appium launcher; default `appium` on the exporter's `PATH` |
| `home` | `APPIUM_HOME` holding the XCUITest driver |
| `capabilities` | Extra capabilities fixed on every session |
| `env` | Extra environment for Appium |
| `startup_timeout`, `max_upload_bytes` | See {doc}`appium` |

Power the simulator on first. Appium starts when `serve()` first asks for its
endpoint, before any session, and stops at power off:

```python
from appium import webdriver
from appium.options.ios import XCUITestOptions

with serve(driver) as simulator:
    simulator.power.on()
    app = simulator.appium.upload_app("build/MyApp.app")
    with simulator.appium.serve() as target:
        options = XCUITestOptions().load_capabilities(target.capabilities())
        options.set_capability("appium:app", app)
        session = webdriver.Remote(target.url, options=options, client_config=target.client_config())
        try:
            print(session.page_source)
        finally:
            session.quit()
```

Inside a `jmp shell`, `j simulator appium` prints the endpoint and
`j simulator appium -- COMMAND` runs a command with it in the environment.

Sessions are pinned to the lease's simulator, its private device set and the
configured WebDriverAgent, whatever the client asks for. Appium can install
apps uploaded with `upload_app()`, launch them by `appium:bundleId` and capture
logs. Capabilities that would erase or shut down the simulator are fixed off,
because they would break the lease's idb companion.

### Optional HTTPS service

A trusted exporter configuration can add an HTTPS service for the lease's
simulator by naming an `HttpServiceProvider` class and its options in the
simulator's `config`:

```yaml
http_service:
  provider: your_package.services.YourProvider
  options: {}
```

Clients use the generic `ios.https()` context manager or
`j simulator ios https -- COMMAND`; they cannot select the provider, its
executable, host address or options. The service starts when HTTPS metadata is
requested after `power.on()`.

```python
with simulator.ios.https() as endpoint:
    url = endpoint.url
    context = endpoint.ssl_context()
    metadata = endpoint.metadata
```

The endpoint exposes its URL, public CA file and the provider's metadata. Keep
certificate and hostname verification enabled. The CLI passes
`JUMPSTARTER_IOS_HTTPS_URL`, `JUMPSTARTER_IOS_HTTPS_CA_FILE` and
`JUMPSTARTER_IOS_HTTPS_METADATA` to the command. The client API is shared with
the {doc}`iOS device driver <iosdevice>`.


## Security and limitations

Each lease owns a private device set and companion Unix socket. Lifecycle
commands explicitly select that set; failures retain ownership when cleanup
must be retried. Core lifecycle uses `simctl` without launching Simulator.app or
Device Hub. Only local macOS execution is supported.

The owning exporter holds a lock on its lease directory. If an exporter exits
without cleanup, for example after a crash, the next lease or session reset in
the same `state_dir` shuts down and deletes that lease's simulators, stops
processes still using its directory, such as its companion, Appium or HTTP
service, and removes it. Live leases and directories without a lease lock are never touched.

Simulators share the Mac's network namespace, so arbitrary TCP forwarding is
unavailable. A configured HTTP provider is trusted exporter code and must
restrict access to its simulator. Its constructor must clean partial resources
on failure; its idempotent `close()` must revoke streams and remove owned state.

Appium listens on exporter loopback and reaches clients over the lease through
verified HTTPS; see {doc}`appium` for the requests it rejects. Its loopback port
is plain HTTP, so other processes and accounts on the exporter Mac could reach
it directly. WebDriverAgent's
HTTP server is bound to loopback, and non-listening wildcard reservations stop
its MJPEG broadcaster from serving the simulator's screen to the network.
Standard screenshots remain available. Appium uses a private `HOME` but may open
Simulator.app, and `isHeadless` is fixed off so Appium does not stop an existing
UI process. Use a dedicated exporter for automation; concurrent GUI workflows
are outside this isolation boundary.

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_iossimulator.driver.IosSimulator()

.. autoclass:: jumpstarter_driver_iosdevice.client.IosDeviceClient()
   :members: info, serve, connect, https
   :no-index:

.. autoclass:: jumpstarter_driver_iossimulator.appium.IosSimulatorAppium()

.. autoclass:: jumpstarter_driver_iossimulator.http_service.HttpServiceProvider()
   :members:
```
