# Devices and apps

## Consoles

The console keywords read and write any driver byte stream (a serial port, a
network connection, a QEMU console) like a terminal, following the conventions
of the
[Telnet](https://robotframework.org/robotframework/latest/libraries/Telnet.html)
library and [SSHLibrary](https://marketsquare.github.io/SSHLibrary/SSHLibrary.html):

```robotframework
Open Console    dut.console    alias=serial    prompt=root@dut:~#    timeout=2 min
${boot}=    Console Read Until    Starting kernel
Console Login    root    ${PASSWORD}
${output}=    Console Execute Command    dmesg | tail
Console Should Not Contain    Kernel panic    duration=10s
Console Write Control Character    C
${transcript}=    Get Console Transcript
```

Output is buffered from the moment the console opens, so nothing is lost
between keywords; each read consumes output up to its match. Several consoles
can be open at once, and `Switch Console` changes the current one.
`method=observe` opens a read-only view of a serial port whose write side
another client holds. Everything received is written to
`results/jumpstarter/console-<alias>-<n>.log`. Consoles close at the end of
their `scope` (`suite` by default), with `Close Console`, or with
`Close All Consoles` in a teardown.

Each console buffers up to 4 Mi characters of unread output. Beyond that, the
oldest unread output is dropped and the next read logs a warning; the
transcript file, which `Get Console Transcript` reads, keeps everything.
Regular-expression reads search output as it arrives, so a single match
longer than 64 KiB can be missed.

## Driver contexts, streams and properties

Some client methods are context managers that hold something open, such as a
port forward. A keyword cannot span a `with` block, so `Open Driver Context`
enters one and keeps it open until `Close Driver Context` or the end of its
scope:

```robotframework
${forward}=    Open Driver Context    android.adb    forward_adb    port=${0}    alias=adb
Log    ADB server at ${forward}[0]:${forward}[1]
Close Driver Context    adb
```

Streaming methods, such as power readings, can be endless. Their keywords
return at most `max_items` items. `Collect Driver Stream` reads a stream for a
number of items or a period of time, then cancels it on the exporter. A
[timeout](https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#timeouts)
that interrupts either one also cancels the stream on the exporter:

```robotframework
${readings}=    Dut Power Read    max_items=3
Should Be True    $readings[0].voltage > 0
${items}=    Collect Driver Stream    dut.power    read    count=5    timeout=10s
${description}=    Get Driver Property    dut.power    description
```

Model results become dictionaries. Write `$readings[0].voltage` in
expressions and `${readings[0].voltage}` in arguments;
`${readings}[0].voltage` is not valid Robot Framework syntax.

## Port forwards and remote libraries

`Open Port Forward` forwards a local TCP port to a driver's byte stream, usually
a `TcpNetwork` driver whose exporter reaches a service on the device, and
returns `host:port`. Anything that speaks TCP then works through the
Jumpstarter tunnel: an HTTP API, a database, or a Robot Framework
[remote library](https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#remote-library-interface)
server such as [PythonRemoteServer](https://github.com/robotframework/PythonRemoteServer):

```robotframework
*** Settings ***
Library    jumpstarter_robot.Jumpstarter    exporter_config=${CURDIR}/../exporter.yaml
...        session=remote    auto_import=False
Suite Setup    Connect To The Board

*** Test Cases ***
Board Keywords Run On The Board
    ${temperature}=    Board.Get Temperature
    Should Be True    ${temperature} < 85
    Board.Set Led    on

*** Keywords ***
Connect To The Board
    ${address}=    Open Port Forward    remote    session=remote
    Import Library    Remote    http://${address}    AS    Board
```

The device's server needs no route to the test runner and is reachable only
through the lease. The forward is a driver context: it closes with
`Close Driver Context`, at the end of its `scope`, or with its session.
The `python/examples/robot/remote-library` example runs this with plain
`robot`, using a small remote library server that stands in for the board.

## Linux, Android and iOS devices

Each device is reached over its native control channel through a Jumpstarter
tunnel, where the usual libraries and tools work:

```robotframework
*** Settings ***
Library    SSHLibrary
Resource    ../client.resource

*** Test Cases ***
Linux Over SSH
    Open SSH Connection    dut.ssh    alias=dut
    ${kernel}=    Execute Command    uname -r
    Put File    app.tar.gz    /tmp/
    Close SSH Connection

Android Over ADB
    Open ADB Device    emulator    timeout=5 min
    ${model}=    ADB Get Property    ro.product.model
    ADB Install    ${CURDIR}/app.apk
    ${log}=    ADB Logcat    lines=100

iOS Tools
    Open iOS Device    simulator
    ${info}=    Run iOS Tool    idb    describe
```

- **SSH**: `Open SSH Connection` forwards a port to an `SSHWrapper` driver,
  like `j ssh`, and logs
  [SSHLibrary](https://marketsquare.github.io/SSHLibrary/SSHLibrary.html) in
  with the exporter's key or a password. `Execute Over SSH` runs a single
  command with the local `ssh` binary; `Get SSH Endpoint` returns the host,
  port, user and key file for `scp` or `rsync`.
- **ADB**: `Open ADB Device` uses the exporter's ADB server through
  [adbutils](https://github.com/openatx/adbutils), so no local `adb` is
  needed, or adds a device's adbd to your local server. The `ADB ...` keywords
  act on the current device; `Get ADB Environment` returns the variables that
  point your own `adb`, Gradle or [Maestro](https://maestro.mobile.dev/) at
  it.
- **iOS**: `Open iOS Device` serves the device's transport on a loopback port.
  `Run iOS Tool` runs `idevice*`, [go-ios](https://github.com/danielpaulus/go-ios),
  [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) or
  [idb](https://fbidb.io/) against it, and the `iOS ...` keywords wrap common
  `idb` commands.

`Connect To Device` picks the connection from the driver's class. These
drivers also get their own `<Prefix> Connect` keyword, such as
`Emulator Connect`. The `python/examples/robot/linux-ssh` example runs
SSHLibrary against any machine with an SSH server through a local exporter.

## Appium apps

The [guide](../../../getting-started/guides/robot-framework.md#mobile-app-testing-with-appium)
shows how `Open Appium Application` hands a verified WebDriver session to
[AppiumLibrary](https://github.com/serhatbolsu/robotframework-appiumlibrary).
In addition:

- An `app=` argument with a local `.apk`, `.ipa` or `.app` uploads it to the
  exporter first; `Upload App` uploads explicitly and returns a
  `jumpstarter-app:` token.
- With `session=`, `Open Appium Application` uses only that session's Appium
  servers.
- For an iPhone in Xcode's Device Hub, `Start Device Hub Appium` runs
  WebDriverAgent and Appium on your Mac.

## One set of tests for Android and iOS

`python/examples/robot/mobile` tests apps on an Android emulator
([UiAutomator2](https://github.com/appium/appium-uiautomator2-driver)) and an
iOS simulator ([XCUITest](https://github.com/appium/appium-xcuitest-driver))
with shared keywords. Each platform is its own client and session, and
aliases give both the same keyword prefixes:

```toml
[tool.jumpstarter.client.android]
exporter-config = "exporter-android.yaml"
resource = "client-android.resource"

[tool.jumpstarter.client.android.aliases]
"emulator.power" = "Device Power"
"emulator.appium" = "Appium"

[tool.jumpstarter.client.ios]
exporter-config = "exporter-ios.yaml"
resource = "client-ios.resource"

[tool.jumpstarter.client.ios.aliases]
"simulator.power" = "Device Power"
"simulator.appium" = "Appium"
```

The emulator and simulator drivers create their `power` and `appium` children
at run time, so the example also commits each client's `nodes` table (see
[Client resources](client-resources.md#without-the-exporter-config-a-committed-driver-mapping)).
`Device Power On` and `Appium Open Application` then mean the same on both
platforms, and the suite directory selects the platform:

```text
resources/common.resource     shared user keywords: boot the device, open Settings, search it
resources/android.resource    AppiumLibrary, client-android.resource, common.resource, Android locators
resources/ios.resource        AppiumLibrary, client-ios.resource, common.resource, iOS locators
tests/android/__init__.robot  Suite Setup and Teardown, Test Tags mobile android
tests/ios/__init__.robot      Suite Setup and Teardown, Test Tags mobile ios
```

Each suite file imports its platform's resource, so any file or folder runs
on its own: `robot tests/` runs both platforms, `robot tests/android` one, and
the RobotCode buttons work without `-v` flags. Each platform's suite setup
skips its tests, with the reason, when local tooling is missing (the iOS
simulator needs macOS), and its teardown stops its exporter with
`Stop Local Exporter`, so only one emulator or simulator runs at a time. The
tests are tagged `mobile`; `--exclude mobile` skips them on machines without
the tooling.

For remote exporters, create a lease for each platform
(`jmp create lease -l board=pixel --duration 2h`) and give each platform's
session the same selector or the lease's name, such as
`-v JMP_ANDROID_SELECTOR:board=pixel -v JMP_IOS_LEASE_NAME:<name>`; each
session then attaches to its lease, and the suites skip the local tooling
checks. Inside `jmp shell`, both sessions would share the shell's single
exporter, so run one platform folder per shell.
