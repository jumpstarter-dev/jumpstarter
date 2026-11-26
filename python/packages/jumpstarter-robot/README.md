# Robot Framework

`jumpstarter-robot` connects [Robot Framework](https://robotframework.org/)
test runs to Jumpstarter {term}`exporter`s. The package makes keywords from
the client class of each driver, such as `Dut Power On` and `Storage Flash`.
It also gives keywords for {term}`lease`s and sessions.

A test file imports one `Driver` library for each driver. Your editor reads
these imports and shows each keyword. A lease is not necessary.

For a step-by-step introduction, refer to the
[Robot Framework](../../../getting-started/guides/robot-framework.md) guide.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-robot
```

Install the driver packages of your exporter in the same environment. The
keywords come from the client classes of these packages.

## Libraries

The package has three Robot Framework libraries. Import them in a test file,
or in a
[resource file](https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#resource-files)
that your test files share.

### `jumpstarter_robot.Jumpstarter`

This library gives the keywords for sessions, leases and driver access. Refer
to [Keywords](#keywords). If the import has arguments, it declares a session.
If the import has no arguments, it only gives the keywords.

| Argument | Default | Description |
| --- | --- | --- |
| `exporter_config` | | A local exporter config. Give a path, or an alias from `jmp config exporter list`. The test run starts this exporter in its own process ({term}`local mode`). |
| `selector` | | The {term}`label selector` of the lease, such as `board=rpi4`. |
| `exporter_name` | | The name of the exporter of the lease. |
| `lease_name` | | The name of an existing lease. The test run does not release this lease. |
| `config` | | The client config for leases. Give an alias from `jmp config client list`, a YAML path, or `env`. |
| `session` | `default` | The name of the session that these arguments declare. |
| `duration` | `30 minutes` | The duration of a new lease, as a Robot Framework time string. |
| `acquisition_timeout` | | The maximum time to wait for a new lease. |
| `allow_disabled` | `False` | `True` also permits an exporter that an administrator disabled. |
| `auto_import` | `True` | `True` imports a `Driver` library for each driver after the session connects. Refer to [Use without `Driver` imports](#use-without-driver-imports). |
| `on_lease_end` | | What to do when a lease ends during the test run: `fatal` or `fail`. Refer to [When a lease ends](#when-a-lease-ends). |
| `release_grace` | `1 s` | The time to wait after the release of a lease. The exporter then sees the release before the next lease. |
| `reuse_lease` | `True` | `True` uses an active lease of this client that has the same selector. Refer to [Use an active lease again](#use-an-active-lease-again). |

Robot Framework ignores a second import of a library that has different
arguments. Thus one `Jumpstarter` import declares one session at most. Use
`jumpstarter_robot.Session` to declare more sessions.

### Use without `Driver` imports

With `auto_import`, the library connects when Robot Framework imports it.
Then it imports a `Driver` library for each driver of the exporter. The
keyword names are the same as with a `Driver` import (`Dut Power On`).

```robotframework
*** Settings ***
Library    jumpstarter_robot.Jumpstarter    exporter_config=${CURDIR}/exporter.yaml

*** Test Cases ***
Discover
    ${drivers}=    List Drivers
    Dut Power On
```

`Acquire Lease` and `Start Local Exporter` also import the driver keywords of
the session that they open.

If two sessions have a driver with the same prefix, the second session adds
its name to the prefix (`Sim Dut Power On`). `List Driver Keywords` gives the
names in use.

These keywords exist only during the test run. Your editor cannot show them.
Import a `Driver` library for each driver if you want completion in your
editor.

### `jumpstarter_robot.Session`

This library declares one session with a name. Thus one test file can declare
many sessions:

```robotframework
Library    jumpstarter_robot.Jumpstarter
Library    jumpstarter_robot.Session    rpi4    selector=board=rpi4
...    AS    session.rpi4
```

The first argument is `name` (default `default`). The other arguments are the
same as the session arguments of `Jumpstarter`: `exporter_config`, `selector`,
`exporter_name`, `lease_name`, `config`, `duration`, `acquisition_timeout`,
`allow_disabled`, `release_grace`, `reuse_lease` and `on_lease_end`. Here,
`on_lease_end` applies only to this session.

An empty value (`${EMPTY}`) is the same as no value.

A session other than `default` can have no selector, lease name or client
config. Then it uses the variables `${JMP_SELECTOR}`, `${JMP_LEASE_NAME}` and
`${JMP_CLIENT_CONFIG}` from the command line, such as
`robot -v JMP_SELECTOR:board=rpi5 tests/`.

Each session gets two keywords that have its name. For the session `rpi4`:

| Keyword | Description |
| --- | --- |
| `Connect Rpi4 Session` | Opens the session immediately and returns the exporter name. |
| `Release Rpi4 Session` | Closes the session. If the session got its lease with `Acquire Lease`, this also releases the lease. |

### `jumpstarter_robot.Driver`

This library gives the keywords of one driver. It reads the keywords from the
client class of the driver. Thus libdoc and your editor can list them without
a lease.

During a test run, the library connects to the driver at `path` when you call
the first keyword. It also makes sure that the driver has the correct client
class.

```robotframework
Library    jumpstarter_robot.Driver    dut.power    client=jumpstarter_driver_power.client.PowerClient
...    prefix=Dut Power    session=default    AS    dut.power
```

| Argument | Default | Description |
| --- | --- | --- |
| `path` | `""` | The path of the driver in the exporter tree, with dots. `dut.power` is `client.dut.power` in Python. An empty path is the root. |
| `client` | | The client class, as `package.module.Class` or `package.module:Class`. |
| `prefix` | words of `path` | The words at the start of each keyword name. With an empty prefix, the keyword is only the method name. |
| `session` | the session of the test file | The session of the driver. |
| `required` | `True` | `False` identifies an optional driver. If the exporter does not have it, the error message says that the driver is optional. |
| `methods` | | The shell driver methods that get their own keywords. Separate the names with commas. |
| `role` | | Makes this library a role library. `Bind Driver` and `Use Device` connect a role library to a different driver. |
| `include` | | Glob patterns. The library makes keywords only for the client members that match. |
| `exclude` | | Glob patterns. The library makes no keywords for the client members that match. |
| `max_items` | `1000` | The default maximum number of items that a stream keyword returns. |

#### Keyword names

A keyword name is the prefix and then the words of the method name. The
default prefix is the full path of the driver.

| Driver path | Method | Keyword |
| --- | --- | --- |
| `dut.power` | `cycle` | `Dut Power Cycle` |
| `storage` | `flash` | `Storage Flash` |

To use a different prefix, give the `prefix` argument. It replaces all words
of the path. For example, `prefix=Device Power` for `dutlink.power` gives
`Device Power On`.

A role library uses the name of the role as its default prefix. The role
`power` gives `Power On`.

You can also use the full name of Robot Framework, with the `AS` name of the
library: `rpi4.dutlink.power.Dutlink Power Cycle`.

The library makes keywords from client members as follows:

| Member | Keyword |
| --- | --- |
| Method | `<Prefix> <Method Words>`. The signature gives the arguments, defaults and types. The docstring gives the documentation. |
| Generator (a stream) | The same name. The keyword returns a list of `max_items` items at most. Then it stops the stream. |
| Property | `Get <Prefix> <Name>`. |
| Context manager | No keyword. |
| `async` method | No keyword. |
| Shell driver | `<Prefix> Run Method`, and one keyword for each method in `methods`. Positional arguments go to the method. Named arguments become environment variables. `expected_rc` (default `0`, or `any`) is the return code that the keyword expects. |

The library converts each argument to the type in the type hint. For a
pydantic model or a dataclass, you can give a dictionary or a JSON string.

A result that is a pydantic model or a dataclass becomes a dictionary. You can
read its values as attributes (`${result.stdout}`). The library saves an image
in the output directory and returns its path.

Each keyword has the tags `jumpstarter`, `driver:<path>` and
`class:<ClientClass>`, and the tags of its `@keyword` decorator.

## Sessions and leases

A session is the connection to the drivers of one exporter. A test run gets a
lease in one of three ways:

1. **You make the lease before the test run.** Use
   `jmp create lease -l board=rpi4 --duration 4h`. The session then finds the
   lease by its name or by its selector.
2. **You use `jmp shell`.** With
   `jmp shell --selector board=rpi4 -- robot ...`, each session uses the lease
   of the shell.
3. **The tests get the lease.** Use `Acquire Lease`, for example in the suite
   setup of an `__init__.robot` file. Only this keyword gets a new lease.

### How a session opens

A session opens when the first keyword uses it. It uses the first source that
applies:

1. The exporter of the `jmp shell` (`JUMPSTARTER_HOST`). In a shell, each
   session uses this exporter.
2. A lease name: `lease_name`, or the `JMP_LEASE` environment variable.
3. An active lease of the client config that has the same `selector` or
   `exporter_name`.
4. `exporter_config`: the test run starts this exporter in its own process
   ({term}`local mode`). This is not a lease.

A session does not get a new lease when it opens. If the session has a
`selector` and there is no lease with that selector, the keyword fails. The
error message gives the three ways to get a lease.

### `Acquire Lease`

`Acquire Lease` connects a session in the same way. But if there is no lease
name and no lease with the same selector, it gets a new lease.

- For a session that has only an `exporter_config`, it starts that exporter
  and returns no lease name. Thus the same suite setup operates with local
  drivers and with a lab.
- For a session that is connected, it returns the lease of that session. If
  you give a different `selector`, `exporter_name` or `lease_name`, it fails.

`Acquire Lease` uses the arguments of the session import. The arguments that
you give replace those values.

### Which session a keyword uses

A keyword without a `session` argument uses the first of these:

1. The session that `Switch Session` set. This stays set until the end of the
   test file that called it.
2. The only session that the test file declares.
3. The session `default`.

Thus a test file that declares one session does not give a session
name.

### Which client config a session uses

A session uses the first of these:

1. The `config` argument of the import or of `Acquire Lease`.
2. The client config from `JMP_ENDPOINT`, `JMP_TOKEN` and the other `JMP_*`
   environment variables, if they are complete.
3. The current client of `jmp config client use`.

This is the same sequence that the `jmp` command uses.

`config=env` uses only the environment variables.

### When the test run releases a lease

The test run releases a lease that `Acquire Lease` got. This occurs when the
session closes:

- at the end of the test run
- with `Release Lease`
- with `Stop Local Exporter`
- with `Release <Name> Session`

The test run does not release a lease that it did not get: a lease from
`lease_name`, from `JMP_LEASE`, from `jmp shell`, or an active lease that it
found.

A `[Timeout]` or Ctrl-C stops a session that waits for a lease. The test
fails.

`robot --dryrun` does not connect, get a lease or start an exporter.

### Use an active lease again

With `reuse_lease` (the default), a session looks for an active lease of the
client. The session must have a `selector` or an `exporter_name`, and no lease
name. The session uses a lease when all of these are true:

- The client owns the lease, or the lease is shared with the client.
- The selector of the lease is equivalent to the selector of the session. The
  labels can be in a different sequence. `a!=x,a!=y` is the same as
  `a notin (x,y)`. A selector with more or fewer labels is not equivalent.
- For a session with an `exporter_name`, the lease is for that exporter.

If more than one lease applies, the session uses the lease that started last.
The test run shows
`Jumpstarter: reusing lease <name> for session '<session>'`.

If the session cannot list the leases, it writes a warning. Then
`Acquire Lease` gets a new lease, and a session that opens without
`Acquire Lease` fails.

In one test run, two sessions do not use the same lease. Thus two sessions
with the same selector each get their own exporter.

Each Robot Framework process has its own leases. With a parallel runner such
as pabot, two processes can find the same active lease. To prevent this, give
the processes different selectors, or set `reuse_lease=False` on the import,
or use `Acquire Lease    reuse=False`.

### When a lease ends

A lease can end, move to a different client, or be revoked during the test
run. By default the test run stops (`fatal`). With `on_lease_end=fail`, only
the keyword fails.

Put the setting in the `__init__.robot` file of the test directory:

```robotframework
*** Settings ***
Library    jumpstarter_robot.Jumpstarter    on_lease_end=fail
```

A `Jumpstarter` import with this argument sets it for the full test run. An
import without the argument does not change it. If two imports give different
values, the last value applies and the library writes a warning.

`on_lease_end` on a `Session` import applies only to that session.

## Keywords

The `Jumpstarter` library gives these keywords. For the full documentation of
each keyword, run
`python -m robot.libdoc jumpstarter_robot.Jumpstarter jumpstarter.html`.

### Sessions and leases

| Keyword | Arguments | Description |
| --- | --- | --- |
| `Acquire Lease` | `selector=None, exporter_name=None, lease_name=None, config=None, duration=None, acquisition_timeout=None, allow_disabled=False, session=None, reuse=None` | Connects the session to a lease. Gets a new lease if no active lease applies. |
| `Connect To Shell Session` | `session=None` | Uses the `jmp shell` that started this test run. Returns the exporter name. |
| `Get Exporter Labels` | `session=None` | Returns the labels of the exporter as a dictionary. |
| `Get Exporter Name` | `session=None` | Returns the name of the exporter. |
| `Get Lease Name` | `session=None` | Returns the lease name, or None for a local exporter. |
| `Lease Should Be Active` | `session=None` | Fails if the session is not connected or its lease ended. |
| `Release Lease` | `session=None` | Closes the session. Releases the lease if `Acquire Lease` got it. |
| `Start Local Exporter` | `exporter_config, session=None` | Starts a local exporter config in this process and connects to it. |
| `Stop Local Exporter` | `session=None` | Stops the exporter of the session and closes the session. |
| `Switch Session` | `session` | Sets the session that keywords use by default. Returns the session that was set before. |

### Driver access

| Keyword | Arguments | Description |
| --- | --- | --- |
| `Bind Driver` | `library, path` | Connects the role library with the `AS` name `library` to the driver at `path`. |
| `Call Driver` | `path, method, *args, session=None, **kwargs` | Calls a client method by its driver path. This includes methods that have no keyword. |
| `Get Driver` | `path, session=None` | Returns the Python client of a driver, for `Call Method` and Python keywords. |
| `List Driver Keywords` | `path="", session=None` | Returns the keyword names of the driver at `path`. |
| `List Drivers` | `session=None` | Lists the drivers of the session and their client classes. |
| `Use Device` | `device, session=None` | Connects each role library to its driver below `device`. |

### Appium

These keywords need the `appium` extra:
`pip install "jumpstarter-robot[appium]"`.

`Start Appium` connects a local HTTPS port to the Appium server of a target.
It returns a dictionary with `url`, `capabilities`, `ca_file` and `alias`.
Give the `url` and the `capabilities` to the `Open Application` keyword of
[AppiumLibrary](https://github.com/serhatbolsu/robotframework-appiumlibrary):

```robotframework
*** Settings ***
Library     AppiumLibrary
Resource    client.resource

*** Test Cases ***
Settings Opens
    ${appium}=    Start Appium    phone.appium
    Open Application    ${appium.url}    &{appium.capabilities}
    ...    appium:appPackage=com.android.settings
    [Teardown]    Run Keywords    Close All Applications    AND    Stop Appium
```

| Keyword | Arguments | Description |
| --- | --- | --- |
| `Start Appium` | `path=appium, port=0, alias=None, scope=global, session=None` | Connects a local HTTPS port to the Appium of the driver at `path`. Returns its URL and capabilities. |
| `Stop Appium` | `alias=None` | Closes the local port. Close the applications first with `Close All Applications`. |
| `Upload App` | `path, file, session=None` | Sends an application file to the Appium at `path`. Returns a token for `appium:app`. |

The exporter starts Appium when you call `Start Appium` the first time. If you
call `Start Appium` again for the same driver, it returns the same server.
The local port closes at the end of `scope`, when the session closes, or with
`Stop Appium`.

### ADB

These keywords need the `android` extra:
`pip install "jumpstarter-robot[android]"`. The `mobile` extra installs
`android` and `appium`.

`Open ADB Device` connects to the Android device of an ADB driver. It uses
[adbutils](https://github.com/openatx/adbutils) through a port forward. Thus a
local `adb` program is not necessary for an ADB server driver (`AdbServer`, or
the `adb` child of an emulator). For an `AdbDevice` driver, a local `adb`
program is necessary.

```robotframework
Open ADB Device    emulator    alias=phone    timeout=5 min
${model}=    ADB Get Property    ro.product.model
ADB Install    ${CURDIR}/app.apk
${output}=    ADB Shell    pm list packages
[Teardown]    Close All ADB Devices
```

You can open more than one device. The other keywords use the current device.
`Switch ADB Device` changes the current device. A device closes at the end of
its `scope`, when its session closes, or with `Close ADB Device`.

| Keyword | Arguments | Description |
| --- | --- | --- |
| `Open ADB Device` | `path, alias=None, serial=None, timeout=180s, wait_for_boot=True, scope=suite, session=None` | Connects to the Android device of the ADB driver at `path` and makes it the current device. |
| `Switch ADB Device` | `alias` | Makes the device with `alias` (or index) the current device. |
| `Close ADB Device` | `alias=None` | Closes the current device, or the device with `alias`. |
| `Close All ADB Devices` | | Closes all devices. |
| `ADB Shell` | `command, timeout=None, expected_rc=0` | Runs `command` in the shell of the device. Returns the output. |
| `ADB Install` | `apk, replace=True, grant_permissions=True` | Installs the APK file `apk`. |
| `ADB Uninstall` | `package` | Removes `package` from the device. |
| `ADB Push` | `local, remote` | Copies the local file `local` to `remote` on the device. |
| `ADB Pull` | `remote, local=None` | Copies `remote` from the device to `local`. Returns the local path. |
| `ADB Get Property` | `name` | Returns the system property `name`. |
| `ADB Wait For Boot` | `timeout=180s` | Waits until the device completes its startup. |

## Change the keywords of a driver

Each public method of a `DriverClient` class is a keyword. You do not change
the driver. To rename, tag or hide a keyword, use the
[`robot.api.deco`](https://robot-framework.readthedocs.io/en/stable/autodoc/robot.api.html)
decorators of Robot Framework on the client class:

| Decorator | Effect |
| --- | --- |
| `@keyword(name=None, tags=(), types=())` | Gives a keyword a name or tags. `name` replaces the words of the method name. The library adds the prefix of the driver. `types` replaces the argument types. `types=None` stops the conversion. |
| `@not_keyword` | Hides a public method from Robot Framework. |
| `@library(auto_keywords=False)` | On the client class: only the methods with `@keyword` become keywords. |

## Completion with RobotCode

[RobotCode](https://robotcode.io/) reads the keywords of each library that a
test file imports. Thus a `Driver` import gives completion and documentation
for each keyword of that driver. A lease is not necessary.

1. Select the Python interpreter of the environment that has
   `jumpstarter-robot` and the driver packages (**Python: Select
   Interpreter**).
2. Import a `Driver` library for each driver, with its `client` class. A
   resource file is a good location for these imports.
3. Import that resource file in each test file
   (`Resource    client.resource`).
4. After you install a new version of a driver package, run **RobotCode: Clear
   Cache and Restart Language Servers**.

To run tests from your editor on a board with a lease:

1. Make the lease one time:
   `jmp create lease -l board=rpi4 --duration 4h`.
2. Give the session the same selector (`selector=board=rpi4`).

Each test run then uses that lease and does not release it. Refer to
[Use an active lease again](#use-an-active-lease-again).

## Limits

- The keywords come from the client classes that are installed. A `Driver`
  import fails if its client class is not installed.
- An `async` client method and a context manager get no keyword.
- `Call Driver` converts text arguments to the types of the client method.
- In `jmp shell`, each session uses the exporter of the shell.
- Keywords from `auto_import` exist only after the session connects.
  `robot --dryrun` does not connect, and thus it cannot find these keywords.
  Test files that import `Driver` libraries operate correctly with `--dryrun`.
- A keyword holds a lock while it communicates with an exporter. Thus keywords
  from different threads run one at a time.
