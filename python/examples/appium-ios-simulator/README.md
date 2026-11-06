# Appium on an iOS Simulator

This example drives the built-in Settings app on an iOS Simulator with
[Appium](https://appium.io/docs/en/latest/), from a pytest suite that uses the
Jumpstarter `JumpstarterTest` fixture. A local exporter creates and boots the
simulator and runs Appium next to it; the tests reach Appium through the lease
with [Appium-Python-Client](https://github.com/appium/python-client), exactly as
they would reach a remote exporter. No app build is needed.

The [Android emulator example](../appium-android-emulator/README.md) runs the
same tests against Android.

## Prerequisites

The exporter runs on macOS and needs:

- The full [Xcode](https://developer.apple.com/xcode/) app as the active
  developer directory, an iOS simulator runtime, and
  [idb_companion](https://github.com/facebook/idb) (`brew install
  facebook/fb/idb-companion`). The
  [iOS Simulator driver](../../packages/jumpstarter-driver-iossimulator/README.md)
  lists the details.
- Node.js, Appium and its XCUITest driver:

  ```bash
  npm install -g appium
  appium driver install xcuitest
  ```

- A simulator build of
  [WebDriverAgent](https://github.com/appium/appium-ios/tree/main/packages/webdriveragent).
  Jumpstarter does not build it. The XCUITest driver ships its project, so
  build that copy:

  ```bash
  cd ~/.appium/node_modules/appium-xcuitest-driver/node_modules/appium-webdriveragent
  xcodebuild build-for-testing -project WebDriverAgent.xcodeproj \
    -scheme WebDriverAgentRunner -destination 'generic/platform=iOS Simulator' \
    -derivedDataPath wda CODE_SIGNING_ALLOWED=NO
  ```

  This produces
  `wda/Build/Products/Debug-iphonesimulator/WebDriverAgentRunner-Runner.app` in
  that directory.

Then edit `exporter.yaml`:

- `device_type` and `runtime`: pick from `xcrun simctl list devicetypes` and
  `xcrun simctl list runtimes`.
- `appium.executable`, `appium.home` and `appium.wda`: only if Appium is not on
  `PATH`, its drivers are not in `~/.appium`, or you built WebDriverAgent
  elsewhere.

## Running the Tests

From this directory, start a local exporter and run pytest inside it:

```bash
jmp shell --exporter-config exporter.yaml -- pytest jumpstarter_example_appium_ios_simulator/ -v
```

Outside a `jmp shell`, `JumpstarterTest` leases an exporter labeled
`example=appium-ios-simulator` with your default client configuration instead.

## What the Tests Demonstrate

| Test                               | Description                                                     |
| ---------------------------------- | --------------------------------------------------------------- |
| `test_settings_is_in_front`        | Open Settings and check it is the app in the foreground         |
| `test_about_shows_the_ios_version` | Open General > About and compare the iOS version with Appium's  |
| `test_screenshot`                  | Take a screenshot through Appium                                |

The simulator boots once for the test class, and all tests share one Appium
session. Each test restarts Settings so it begins on its home screen. A run
takes a minute or two, most of it booting the simulator and starting
WebDriverAgent.

## How It Works

```text
[pytest + Appium-Python-Client] -> [Jumpstarter lease] -> [IosSimulator driver]
        |                                                     |-- power: simulator lifecycle
        |-- client.simulator.power.on()                       |-- ios: idb companion
        |-- client.simulator.appium.serve() -> url, caps      |-- appium: Appium + XCUITest + WDA
        v
   webdriver.Remote(target.url, ...)  <---- HTTPS over the lease ---->  Appium on the exporter
```

`target.capabilities()` already pins the session to this simulator and its
WebDriverAgent, so the tests add nothing device-specific. See the
[Appium UI Automation guide](../../../docs/source/getting-started/guides/ui-automation.md)
for how the shared Appium interface works across targets.
