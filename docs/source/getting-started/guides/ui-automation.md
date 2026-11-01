# UI Automation with Appium

Jumpstarter gives every UI-automation target the same
[Appium](https://appium.io/docs/en/latest/) client interface: an iOS simulator,
an iPhone or iPad on a USB bench, or an iPhone or iPad shown in Xcode's Device
Hub, local or remote. A test asks the target for an Appium endpoint and opens WebDriver
sessions on it; it does not change when the target does.

## How it works

Each target driver adds an `appium` child when the exporter configures one. The
exporter runs Appium for the lease, next to the device, and the client reaches it
over the lease through verified HTTPS. Clients need neither Appium nor the
device's tools.

| Target | Driver | Appium runs | Configure |
| --- | --- | --- | --- |
| iOS simulator | {doc}`iossimulator <../../reference/package-apis/drivers/iossimulator>` | exporter (macOS) | `appium:` with a prebuilt simulator WebDriverAgent |
| iPhone or iPad on USB | {doc}`iosdevice <../../reference/package-apis/drivers/iosdevice>` | exporter | `appium:` with `wda_port` or WebDriverAgent launch capabilities |
| iPhone or iPad in Xcode's Device Hub | {doc}`iosdevice <../../reference/package-apis/drivers/iosdevice>` | this Mac | `devicehub_appium()` with your WebDriverAgent build |

Device Hub is the one case where Appium runs on the client: WebDriverAgent is
reachable only inside the Mac's Device Hub connection, so the Mac starts your
installed Appium. It still yields the same target, so tests do not change.

Every target pins the session to its own device. Capabilities such as
`appium:udid` or the simulator's device set are fixed by the exporter and
replace whatever a client sends.

## Write the test once

```python
from appium import webdriver
from appium.options.common import AppiumOptions

from jumpstarter.common.utils import env

with env() as root, root.phone.appium.serve() as target:
    options = AppiumOptions().load_capabilities(target.capabilities())
    driver = webdriver.Remote(target.url, options=options, client_config=target.client_config())
    try:
        print(driver.page_source)
    finally:
        driver.quit()
```

`root.phone` is the target's export name. For a simulator, power it on first
(`root.simulator.power.on()`) and use `root.simulator.appium`. For Device
Hub, replace `root.phone.appium.serve()` with
`root.phone.devicehub_appium("WebDriverAgentRunner.xctestrun")`.

`target.capabilities()` holds what the target fixes, such as `platformName`,
`appium:automationName` and `appium:udid`. Add your own, for example
`appium:bundleId`, before opening a session.

Install [Appium-Python-Client](https://github.com/appium/python-client) to run
this example. Other WebDriver clients work too: use `target.ssl_context()` or
`target.ca_file` for the lease's certificate authority, and keep certificate
verification enabled.

## Applications

The exporter's Appium cannot read files on your machine. Upload an application
first, then pass the token it returns wherever Appium expects an application:

```python
token = root.phone.appium.upload_app("build/MyApp.ipa")
options.set_capability("appium:app", token)
# or, inside a session:
driver.execute_script("mobile: installApp", {"app": token})
```

## Command line

Inside a `jmp shell`:

```shell
j phone appium                          # print url, ca_file and capabilities until Ctrl+C
j phone appium -- pytest tests/ui       # run a command with the endpoint in its environment
j phone appium upload build/app.ipa     # print an application token
```

The command receives `JUMPSTARTER_APPIUM_URL`, `JUMPSTARTER_APPIUM_CAPABILITIES`
(JSON), `JUMPSTARTER_APPIUM_CA_FILE` and Node's `NODE_EXTRA_CA_CERTS`, so test
runners in other languages can connect too. For iOS targets the export is
usually `ios` (`j ios appium`), and for a simulator the `appium` child sits
next to `power` (`j simulator appium`).

## Prepare the exporter

The exporter needs Node.js, Appium and its
[XCUITest driver](https://github.com/appium/appium-xcuitest-driver).
Jumpstarter does not bundle,
download, build or sign Appium, its drivers or WebDriverAgent.

Containerized exporters pin these in their image. For example, on top of an
exporter image:

```dockerfile
FROM quay.io/jumpstarter-dev/jumpstarter:latest
RUN dnf install -y nodejs npm && dnf clean all
ENV APPIUM_HOME=/opt/appium/home
RUN npm install --prefix /opt/appium appium@3.8.0 && \
    /opt/appium/node_modules/.bin/appium driver install xcuitest@12.22.8
```

Then point the target's `appium` block at that installation:

```yaml
export:
  phone:
    type: jumpstarter_driver_iosdevice.driver.IosDevice
    config:
      usb_port: "1-4.2"
      appium:
        executable: /opt/appium/node_modules/.bin/appium
        home: /opt/appium/home
        wda_port: 8100
```

macOS exporters for simulators install Appium and its XCUITest driver on the
host in the same way. Each driver's page lists its own options.

## Security

A lease controls its device through Appium, and Appium runs inside the exporter.
The {doc}`Appium driver <../../reference/package-apis/drivers/appium>` keeps
it to the device:

- Appium listens on exporter loopback only, with every Appium insecure feature
  denied, no plugins and a private home directory.
- Capabilities that name exporter files, executables or URLs, the `proxy`
  capability and `remotePath` uploads are rejected, so clients cannot make the
  exporter read its files or contact other hosts. Applications are accepted only
  as uploads.
- Target-specific capabilities that would reach beyond the device, such as
  erasing or shutting down the simulator, are fixed off.

Leases still get full control of their device. Reset or re-provision devices
between untrusted tenants, and keep exporters that serve untrusted tenants
dedicated to that role.
