# Appium Driver

`jumpstarter-driver-appium` gives every UI-automation target the same
[Appium](https://appium.io/docs/en/latest/) client interface. Test code opens
WebDriver sessions on one URL with the capabilities the target provides,
whether the target is an iOS device or an iOS simulator, local or remote.

Target drivers add an `appium` child built on `AppiumDriver`. It runs an
operator-installed Appium on the exporter for each lease, listening on exporter
loopback only, and clients reach it over the lease through verified HTTPS.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-appium
```

The exporter needs Node.js, Appium and the Appium drivers for its targets, such
as [XCUITest](https://github.com/appium/appium-xcuitest-driver) for iOS.
Containerized exporters usually pin them in their image; see
{doc}`../../../getting-started/guides/ui-automation` for an example. Jumpstarter
does not bundle, download or build Appium, its drivers or WebDriverAgent.

To use the Python examples below, install
[Appium-Python-Client](https://github.com/appium/python-client) on the client.
The client does not need Appium itself.

## Configuration

Target drivers usually create the `appium` child and fix the capabilities
that identify their device. `AppiumDriver` can also be configured directly:

```yaml
export:
  appium:
    type: jumpstarter_driver_appium.driver.AppiumDriver
    config:
      executable: /opt/appium/node_modules/.bin/appium
      home: /opt/appium/home
      drivers: [xcuitest]
      capabilities:
        platformName: iOS
        appium:automationName: XCUITest
        appium:udid: 00008140-001E05C80013C01C
```

| Parameter | Description | Default |
| --- | --- | --- |
| `executable` | Appium launcher, resolved on the exporter's `PATH` | `appium` |
| `home` | `APPIUM_HOME` holding the installed Appium drivers | inherited |
| `drivers` | Appium drivers to load; all installed ones when empty | `[]` |
| `capabilities` | Capabilities fixed on every session; clients cannot override them | `{}` |
| `env` | Extra environment for Appium, such as `USBMUXD_SOCKET_ADDRESS` | `{}` |
| `state_dir` | Existing directory for the lease's private state | system temporary directory |
| `startup_timeout` | Seconds to wait for Appium to become ready | `60` |
| `max_upload_bytes` | Largest application a client may upload | 4 GiB |

## Usage

```python
from appium import webdriver
from appium.options.common import AppiumOptions

from jumpstarter.common.utils import env

with env() as root, root.appium.serve() as target:
    options = AppiumOptions().load_capabilities(target.capabilities())
    driver = webdriver.Remote(target.url, options=options, client_config=target.client_config())
    try:
        print(driver.page_source)
    finally:
        driver.quit()
```

`serve()` starts the lease's Appium on first use and yields an `AppiumTarget`:

- `url`: a loopback HTTPS address that reaches the exporter's Appium.
- `capabilities()`: the capabilities the target fixes, such as `platformName`
  and `appium:udid`; add your own before opening a session. Host paths stay on
  the exporter and are not shown.
- `client_config()`: an Appium-Python-Client configuration that trusts the
  lease's certificate authority. Use `ssl_context()` or `ca_file` with other
  clients. Certificate and hostname verification stay enabled.
- `environment()`: the caller's environment plus `JUMPSTARTER_APPIUM_URL`,
  `JUMPSTARTER_APPIUM_CAPABILITIES` (JSON), `JUMPSTARTER_APPIUM_CA_FILE` and
  Node's `NODE_EXTRA_CA_CERTS`, for tools written in other languages.

Closing the block removes the listener. Appium keeps running until `stop()`,
reset or the end of the lease.

### Applications

Appium on the exporter cannot read files on the client, so upload applications
first and pass the returned token where Appium expects an application:

```python
token = root.appium.upload_app("build/MyApp.ipa")
options.set_capability("appium:app", token)
# or later, inside a session:
driver.execute_script("mobile: installApp", {"app": token})
```

`upload_app()` accepts `.apk`, `.aab`, `.apks`, `.ipa` and `.zip` files and
`.app` bundles, which it zips first. Uploads last until reset or the end of the
lease.

### Command line

Inside a `jmp shell`, with the child named `appium`:

```shell
j appium                                   # print url, ca_file and capabilities until Ctrl+C
j appium -- pytest tests/ui                # run a command with environment()
j appium upload build/MyApp.ipa            # print an application token
j appium stop
```

## Security and limits

A lease holder controls the target through Appium, so the driver keeps Appium
from reaching beyond it:

- Appium binds exporter loopback, starts with every insecure feature denied and
  no plugins, and gets a private `HOME`.
- Capabilities the target driver fixes replace any value a client sends.
- Capabilities whose string values name files, directories, executables or URLs
  (for example `appium:chromedriverExecutable` or `appium:mjpegScreenshotUrl`),
  the `proxy` capability and any `remotePath` argument are rejected, so clients
  cannot make the exporter read its files or contact other hosts.
- `app`, `otherApps` and `appPath` accept only tokens from `upload_app()`.

Appium runs as a child process of the exporter, in the exporter's container, so
the rules above, not a container boundary, are what keep a lease to its device.
Keep exporter images to what the targets need, and reset or re-provision devices
between untrusted tenants. See {doc}`../../../getting-started/guides/ui-automation`
for an overview of every target.

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_appium.driver.AppiumDriver()

.. autoclass:: jumpstarter_driver_appium.client.AppiumClient()
    :members: serve, upload_app, stop, info

.. autoclass:: jumpstarter_driver_appium.common.AppiumTarget()
    :members: capabilities, ssl_context, client_config, environment
```

Targets that only the client can reach, such as an iOS device shown through
Device Hub, run the user's installed Appium on the client instead and yield the
same `AppiumTarget`:

```{eval-rst}
.. autofunction:: jumpstarter_driver_appium.local.local_appium
```
