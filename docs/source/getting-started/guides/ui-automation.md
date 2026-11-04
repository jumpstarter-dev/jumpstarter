# Appium UI Automation

Jumpstarter gives every UI-automation target the same
[Appium](https://appium.io/docs/en/latest/) client interface, whatever its
platform: physical devices, emulators and simulators, on Android or iOS, local
or remote. A test asks the target for an Appium endpoint and opens WebDriver
sessions on it; it does not change when the target does.

## Why Appium

Appium is Jumpstarter's default UI-automation framework because it suits how
Jumpstarter is used:

- **One API on every platform.** Appium speaks the W3C WebDriver protocol and
  hands each session to a platform driver, such as UiAutomator2 for Android or
  XCUITest for iOS, with further drivers for desktop and TV platforms. Tests use
  the same client calls whichever device they drive, so one suite covers every
  target, and every Jumpstarter target can offer the same interface.
- **Python support.** Jumpstarter clients are written in Python, and the Appium
  project maintains an official Python client,
  [Appium-Python-Client](https://github.com/appium/python-client), built on
  Selenium's Python bindings. It installs with `pip`, works with pytest and other
  Python test runners, and accepts the configuration Jumpstarter hands it
  directly. Clients in other languages, such as Java, JavaScript, Ruby or .NET,
  can connect too.
- **Nothing to install on the client.** Appium and its platform drivers run on
  the exporter, next to the device, so clients need only a WebDriver client
  library: no Appium, Node.js or platform SDKs.
- **Open and established.** Appium is open source under the Apache 2.0 licence,
  hosted by the OpenJS Foundation, and widely used, so existing Appium tests,
  tools and experience carry over.

## How it works

Each target driver adds an `appium` child when the exporter configures one. The
exporter runs Appium for the lease, next to the device, and the client reaches it
over the lease through verified HTTPS. Clients need neither Appium nor the
device's tools.

| Target | Driver | Appium runs | Configure |
| --- | --- | --- | --- |
| Android device | {doc}`adb <../../reference/package-apis/drivers/adb>` | exporter | `appium:` |
| Android emulator | {doc}`androidemulator <../../reference/package-apis/drivers/androidemulator>` | exporter | `appium:` |
| iPhone or iPad on USB | {doc}`iosdevice <../../reference/package-apis/drivers/iosdevice>` | exporter | `appium:` with `wda_port` for an already running WebDriverAgent, or WebDriverAgent launch capabilities |
| iOS simulator | {doc}`iossimulator <../../reference/package-apis/drivers/iossimulator>` | exporter (macOS) | `appium:` with a prebuilt simulator WebDriverAgent |
| iPhone or iPad in Xcode's Device Hub | {doc}`iosdevice <../../reference/package-apis/drivers/iosdevice>` | this Mac | `devicehub_appium()` with your WebDriverAgent build |

Device Hub is the one case where Appium runs on the client: WebDriverAgent is
reachable only inside the Mac's Device Hub connection, so the Mac starts your
installed Appium. It still yields the same target, so tests do not change.

Every target pins the session to its own device. Capabilities such as
`appium:udid`, the device set or the ADB server are fixed by the exporter and
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

`root.phone` is the target's export name. For a simulator or emulator, power it
on first (`root.simulator.power.on()`); Appium waits for it to boot. For Device
Hub, replace `root.phone.appium.serve()` with
`root.phone.devicehub_appium("WebDriverAgentRunner.xctestrun")`.

`target.capabilities()` holds what the target fixes, such as `platformName`,
`appium:automationName` and `appium:udid`. Add your own, for example
`appium:bundleId` or `appium:appPackage`, before opening a session.

If your client configuration allow-lists driver packages under `drivers.allow`,
add `jumpstarter_driver_appium.*`, or the `appium` child cannot load.

Install [Appium-Python-Client](https://github.com/appium/python-client) to run
this example. For complete pytest suites that drive the built-in Settings app
through a local exporter, see `python/examples/appium-android-emulator` and
`python/examples/appium-ios-simulator` in the repository. Other WebDriver clients work too: use `target.ssl_context()` or
`target.ca_file` for the lease's certificate authority, and keep certificate
verification enabled.

## Applications

The exporter's Appium cannot read files on your machine. Upload an application
first, then pass the token it returns wherever Appium expects an application:

```python
token = target.upload_app("build/app-debug.apk")  # or an .ipa, or a .app bundle
options.set_capability("appium:app", token)
# or, inside a session; the argument is appPath on Android and app on iOS:
driver.execute_script("mobile: installApp", {"appPath": token})
driver.execute_script("mobile: installApp", {"app": token})
```

`root.phone.appium.upload_app()` does the same without serving Appium first.
Installed applications start with `appium:bundleId` (iOS) or
`appium:appPackage` (Android). A token works only on the target that issued it.

Device Hub sessions accept the same capabilities, with tokens from the Device
Hub target's own `upload_app()`. Appium on your Mac cannot install applications
over Device Hub, so Jumpstarter installs them with `xcrun devicectl` before
Appium sees the request, resetting an installed copy as Appium does, and starts
an `appium:app` by its bundle identifier. Web views, MJPEG screen streaming and
`mobile: listApps` are not available over Device Hub; see
{doc}`iosdevice <../../reference/package-apis/drivers/iosdevice>`.

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
usually `ios` (`j ios appium`), and for a simulator or emulator the `appium`
child sits next to `power` (`j simulator appium`).

## Robot Framework

The `jumpstarter-robot` package drives these targets from
[Robot Framework](https://robotframework.org/) with
[AppiumLibrary](https://github.com/serhatbolsu/robotframework-appiumlibrary):

```robotframework
*** Settings ***
Library     AppiumLibrary
Resource    client.resource

*** Test Cases ***
Open Settings
    ${appium}=    Start Appium    phone.appium
    Open Application    ${appium.url}    &{appium.capabilities}    appium:appPackage=com.android.settings
    Capture Page Screenshot
```

`Start Appium` serves the target's Appium and returns its `url` and the
capabilities the target fixes, for AppiumLibrary's `Open Application`.
`Upload App` uploads applications. See the
[Robot Framework guide](robot-framework.md#mobile-app-testing-with-appium)
for Android emulator and iOS simulator suites.

## Prepare the exporter

The exporter needs Node.js, Appium and the Appium driver for its targets:

- Android: [UiAutomator2](https://github.com/appium/appium-uiautomator2-driver),
  a JDK, and the Android SDK's platform tools and build tools, with `JAVA_HOME`
  and `ANDROID_HOME` set in the exporter's environment.
- iOS: [XCUITest](https://github.com/appium/appium-xcuitest-driver).

Jumpstarter does not bundle, download, build or sign Appium, its drivers or
WebDriverAgent.

Containerized exporters pin these in their image. For example, on top of an
exporter image:

```dockerfile
FROM quay.io/jumpstarter-dev/jumpstarter:latest
RUN dnf install -y nodejs npm java-21-openjdk-headless && dnf clean all
# For Android targets, install the SDK's platform-tools and build-tools here, for
# example with sdkmanager, and set JAVA_HOME to the JDK.
ENV ANDROID_HOME=/opt/android-sdk
ENV APPIUM_HOME=/opt/appium/home
RUN npm install --prefix /opt/appium appium@3.8.0 && \
    /opt/appium/node_modules/.bin/appium driver install uiautomator2@8.7.3 && \
    /opt/appium/node_modules/.bin/appium driver install xcuitest@12.22.8
```

Then point the target's `appium` block at that installation:

```yaml
export:
  phone:
    type: jumpstarter_driver_adb.driver.AdbDevice
    config:
      usb_port: "1-4.2"
      appium:
        executable: /opt/appium/node_modules/.bin/appium
        home: /opt/appium/home
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
  starting emulators or another ADB host on Android, or erasing the simulator,
  are rejected or fixed off.

Leases still get full control of their device. Reset or re-provision devices
between untrusted tenants, and keep exporters that serve untrusted tenants
dedicated to that role.
