# Mobile app testing with Appium

Robot Framework suites that test mobile apps on an **Android emulator** and an
**iOS simulator** through Jumpstarter: the exporter runs the device and
[Appium](https://appium.io/docs/en/latest/), and the tests mix device control
(power, ADB, idb) with UI automation through
[AppiumLibrary](https://github.com/serhatbolsu/robotframework-appiumlibrary).

The platform comes from the **suite folder**: `tests/android` and `tests/ios`
each import their own platform resource, so any file or folder runs as it is,
from `robot` or from the [RobotCode](https://robotcode.io/) buttons, with no `-v` flags. All tests are
tagged `mobile` plus `android` or `ios`. This is a separate example from
the [basic one](../basic/README.md), whose run does not include these suites.

## Layout

| Path | |
| --- | --- |
| `robot.toml` | RobotCode settings, and the `android` and `ios` clients (`[tool.jumpstarter.client.<name>]`) with their aliases and committed `nodes` |
| `exporter-android.yaml` | `AndroidEmulator` (AVD `jumpstarter_test`) with an `appium` block; a template, see below |
| `exporter-ios.yaml` | `IosSimulator` with an `appium` block and a prebuilt WebDriverAgent; a template |
| `client-android.resource`, `client-ios.resource` | Generated client resources, sessions `android` and `ios` |
| `resources/common.resource` | The platform-neutral flows: prepare and release the device, open Settings, search it |
| `resources/android.resource`, `resources/ios.resource` | Each platform's client, locators, capabilities, tooling checks and diagnostics |
| `tests/android/`, `tests/ios/` | `settings.robot` (built-in Settings app), `sample_app.robot` (Appium's sample app), `device_and_ui.robot` (power cycle plus UI), and `adb.robot` / `ios_tools.robot` (native tools through the tunnel) |
| `scripts/fetch-sample-apps.sh` | Downloads the pinned sample apps into `apps/`, checking their SHA256 |

Both clients alias their power driver to `Device Power` and their Appium
driver to `Appium`, so `common.resource` says `Device Power On` and `Appium
Open Application` and each suite resolves them against its own device.

## Prerequisites

Android:

- the Android SDK with `emulator` and `platform-tools`, and `ANDROID_HOME` (or
  `ANDROID_SDK_ROOT`) set
- an AVD named `jumpstarter_test` (or set `AVD_NAME`), for example
  `avdmanager create avd -n jumpstarter_test -k "system-images;android-35;google_apis;arm64-v8a"`

iOS (macOS only):

- the full Xcode selected (`sudo xcode-select -s /Applications/Xcode.app`) with
  an iOS 17.4+ simulator runtime
- [idb_companion](https://github.com/facebook/idb) on `PATH` (or set
  `IDB_COMPANION`); `ios_tools.robot` also needs the `idb` client
  (`pip install fb-idb`)
- a complete prebuilt **simulator** `WebDriverAgentRunner-Runner.app`, in
  `WDA_APP`. Jumpstarter never builds or signs WebDriverAgent.

Both:

- Node 22 and Appium 2 or 3 (`npm install -g appium`, or set
  `APPIUM_EXECUTABLE`), with the `uiautomator2` and `xcuitest` drivers
  installed in `APPIUM_HOME` (default `~/.appium`):
  `APPIUM_HOME=~/.appium appium driver install uiautomator2`

The tests themselves only need the workspace venv: `uv sync --all-packages
--all-extras` in `python/` installs `jumpstarter-robot[mobile]` and
AppiumLibrary.

## Running

```console
$ cd python/examples/robot/mobile
$ uv run robot -d results tests/                  # both platforms, one after the other
$ uv run robot -d results tests/android           # one platform
$ uv run robot -d results tests/ios/settings.robot
```

Each platform folder boots its device once, in the folder's
`__init__.robot`, and shuts it down at the end; a single file run on its own
does the same for itself. When the tooling is missing, the suite setup skips
every test of that platform with the reason, for example
`ANDROID_HOME is not set` or `The iOS simulator needs macOS`.

`exporter-android.yaml` and `exporter-ios.yaml` are templates: before serving
one, the suite fills in its placeholders from Robot Framework variables, which
default to the environment variables of the same name:

| Variable | Default |
| --- | --- |
| `ANDROID_HOME` | `ANDROID_SDK_ROOT` |
| `AVD_NAME` | `jumpstarter_test` |
| `APPIUM_EXECUTABLE`, `APPIUM_HOME` | `appium`, `~/.appium` |
| `IOS_DEVICE_TYPE` | `com.apple.CoreSimulator.SimDeviceType.iPhone-16` |
| `IOS_RUNTIME` | the newest installed iOS runtime |
| `IDB_COMPANION` | `idb_companion` |
| `WDA_APP` | (required) |

Against a remote exporter, or inside `jmp shell`, the device and Appium run
on the exporter, so none of the local checks apply and no template is served.
These suites never acquire a lease: run them inside `jmp shell`, or attach
them to a lease you created.

Inside `jmp shell` both sessions use the shell's one exporter, so run one
platform folder per shell:

```console
$ uv run jmp shell --selector board=pixel -- robot -d results tests/android
```

To attach to a lease you created, give the platform's session its selector or
the lease name. Each platform is its own session, with its own variables:

```console
$ uv run jmp create lease -l board=pixel --duration 2h
$ uv run robot -d results -v JMP_ANDROID_SELECTOR:board=pixel tests/android
$ uv run robot -d results -v JMP_IOS_LEASE_NAME:<name> tests/ios   # a simulator lease, by name
```

A selector attaches to the active lease of your client for exactly that
selector. The run never releases the lease, so it serves every run until you
delete it, including runs from the RobotCode buttons once `robot.toml` sets
`JMP_ANDROID_SELECTOR` (or `JMP_IOS_SELECTOR`) under `[variables]`. With a selector and no matching lease, the
suite setup fails with a message naming the ways to get one.

Name the lease with `JMP_ANDROID_LEASE_NAME` or `JMP_IOS_LEASE_NAME` rather
than the `JMP_LEASE` environment variable: the suites decide from the session
variables whether the device is remote, and otherwise check for local tooling
and serve the template.

In VS Code, open `mobile/` as its own workspace folder (RobotCode reads the
`robot.toml` of the folder it opens); `.vscode/settings.json` selects the
`python/.venv` interpreter.

## Sample apps

`settings.robot` and `device_and_ui.robot` use the built-in Settings apps and
need no download. `sample_app.robot` (and the install tests of `adb.robot`
and `ios_tools.robot`) use Appium's public sample apps, ApiDemos for Android
and UIKitCatalog for the iOS simulator, and skip while they are missing:

```console
$ ./scripts/fetch-sample-apps.sh
```

The script downloads pinned releases into `apps/` (ignored by git) and checks
each file's SHA256. The pins (ApiDemos v6.0.18, UIKitCatalog v4.0.5) are
maintained by hand at the top of the script.

The suites show both ways to install an app: Android uploads explicitly with
`Appium Upload App` and passes the returned `jumpstarter-app:` token as
`app=`; iOS passes the local zip as `app=` and lets `Appium Open Application`
upload it.

## Regenerating the clients

The emulator and the simulator create their `adb`, `ios`, `power` and `appium`
children at run time, so the exporter configs do not list them and the clients
are generated from the `nodes` tables committed in `robot.toml`:

```console
$ uv run jmp robot generate
```

When a driver changes, record the tree from a running exporter instead of
editing the tables by hand: `jmp robot generate --capture --client android`
inside `jmp shell`, or with `--from` and a `jmp describe
lease --drivers -o json` dump. `--check` reports drift without writing.

## Troubleshooting

- **Appium fails to start a session on Android**: check that
  `appium driver list --installed` (with the same `APPIUM_HOME`) shows
  `uiautomator2`.
- **The AVD is not found**: `$ANDROID_HOME/emulator/emulator -list-avds` must
  show it.
- **iOS sessions time out**: `WDA_APP` must be a *simulator* build of
  WebDriverAgent matching your Xcode; a device build does not launch.
- **Locators**: Settings layouts differ between Android and iOS versions. The
  locators live in the platform resources (`${SETTINGS_SEARCH}`,
  `${SETTINGS_SEARCH_FIELD}`); adjust them there.
