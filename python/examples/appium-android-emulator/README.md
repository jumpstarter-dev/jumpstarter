# Appium on an Android Emulator

This example drives the built-in Settings app on an Android emulator with
[Appium](https://appium.io/docs/en/latest/), from a pytest suite that uses the
Jumpstarter `JumpstarterTest` fixture. A local exporter boots the emulator and
runs Appium next to it; the tests reach Appium through the lease with
[Appium-Python-Client](https://github.com/appium/python-client), exactly as they
would reach a remote exporter or a physical phone. No APK is needed.

The [iOS Simulator example](../appium-ios-simulator/README.md) runs the same
tests against iOS.

## Prerequisites

- An Android SDK with the emulator and platform tools, and a test AVD. The
  [Android emulator example](../android-emulator/README.md)'s `setup.sh` finds
  the SDK, creates the `jumpstarter_test` AVD and puts `emulator` on `PATH`:

  ```bash
  source ../android-emulator/setup.sh
  ```

- Node.js, Appium and its UiAutomator2 driver, which the exporter runs:

  ```bash
  npm install -g appium
  appium driver install uiautomator2
  ```

- A JDK, with `JAVA_HOME` and `ANDROID_HOME` set in the shell that starts the
  exporter.

`exporter.yaml` expects `appium` on `PATH` and its drivers in `~/.appium`, where
the commands above put them. Edit its `appium` block if yours are elsewhere.

## Running the Tests

From this directory, start a local exporter and run pytest inside it:

```bash
jmp shell --exporter-config exporter.yaml -- pytest jumpstarter_example_appium_android_emulator/ -v
```

Outside a `jmp shell`, `JumpstarterTest` leases an exporter labeled
`example=appium-android-emulator` with your default client configuration
instead.

## What the Tests Demonstrate

| Test                                   | Description                                                         |
| -------------------------------------- | ------------------------------------------------------------------- |
| `test_settings_is_in_front`            | Open Settings and check it is the app in front                      |
| `test_about_shows_the_android_version` | Open About and compare the Android version shown with Appium's      |
| `test_screenshot`                      | Take a screenshot through Appium                                    |

The emulator boots once for the test class, and all tests share one Appium
session. Each test starts Settings from its home screen.

## How It Works

```text
[pytest + Appium-Python-Client] -> [Jumpstarter lease] -> [AndroidEmulator driver]
        |                                                     |-- power: emulator process
        |-- client.android.power.on()                         |-- adb: private ADB server
        |-- client.android.appium.serve() -> url, capabilities|-- appium: Appium + UiAutomator2
        v
   webdriver.Remote(target.url, ...)  <---- HTTPS over the lease ---->  Appium on the exporter
```

`target.capabilities()` already pins the session to this emulator, so the
tests add nothing device-specific. See the
[Appium UI Automation guide](../../../docs/source/getting-started/guides/ui-automation.md)
for how the shared Appium interface works across targets.
