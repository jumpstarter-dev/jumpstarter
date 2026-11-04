# Robot Framework

`jumpstarter-robot` connects [Robot Framework](https://robotframework.org/)
runs to Jumpstarter {term}`exporter`s. Every driver of the exporter gets its own
keywords, such as `Dut Power On` or `Storage Flash`, and the package adds keywords
for {term}`lease`s, serial consoles, driver streams, Appium, SSH, ADB and iOS
connections. A generator writes a client resource per exporter, so editors list
every keyword, with its arguments and documentation, without a lease.

For a walkthrough, see the
[Robot Framework](../../../getting-started/guides/robot-framework.md)
guide.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} "jumpstarter-robot[mobile,ssh]"
```

Install the driver packages of your exporter in the same environment: keywords
come from their client classes. The extras add the libraries some keyword sets
use:

| Extra | Installs | For |
| --- | --- | --- |
| `ssh` | SSHLibrary | `Open SSH Connection` and SSHLibrary keywords on leased Linux devices |
| `android` | adbutils | the ADB keywords |
| `appium` | AppiumLibrary, `jumpstarter-driver-appium` | `Start Appium` and AppiumLibrary keywords |
| `mobile` | `android` and `appium` | Android and iOS app testing |

Without an extra, its keywords still exist and are documented; they fail with
an install hint when they run.

## Libraries

The package has three Robot Framework libraries. Generated client resources
import all three; a suite can also import them itself.

### `jumpstarter_robot.Jumpstarter`

Sessions, leases and the keywords that work across drivers (see
[Keywords](#keywords)). With arguments, the import declares a session; without,
it only provides the keywords.

| Argument | Default | Description |
| --- | --- | --- |
| `exporter_config` | | Local exporter config, by path or by alias from `jmp config exporter list`, served in this process ({term}`local mode`) |
| `selector` | | {term}`label selector`, such as `board=rpi4`, of the lease to attach to, or to acquire with `Acquire Lease` |
| `exporter_name` | | Exporter of the lease to attach to, or to acquire with `Acquire Lease` |
| `lease_name` | | Existing lease to attach to; it is never released by the run |
| `config` | | Client config to attach to or acquire leases with: an alias from `jmp config client list`, a client config YAML path, or `env` |
| `session` | `default` | Name of the session these arguments declare |
| `duration` | `30 minutes` | Lease duration, as a Robot Framework time string |
| `acquisition_timeout` | | How long to wait for a lease to be acquired |
| `allow_disabled` | `False` | Also lease an exporter an administrator has disabled |
| `auto_import` | `True` | Import a `Driver` library for every driver once connected (see Zero configuration below) |
| `on_lease_end` | | When a lease ends, is transferred or is revoked during the run: `fatal` stops the run, `fail` fails the keyword only. It applies to the whole run; without it, the run's policy (`fatal` unless an import set it) stays as it is (see [When a lease ends](#when-a-lease-ends)) |
| `release_grace` | `1 s` | Pause after releasing a lease, so the exporter sees the release before the next lease of the same device |
| `exporter_class` | | Reserved for leasing by ExporterClass; not supported yet, and fails when set |
| `reuse_lease` | `True` | Attach to an active lease of this client for exactly the same selector; when off, the session attaches only to a named lease, and `Acquire Lease` always acquires a new one (see [Reusing a lease](#reusing-a-lease)) |

Robot Framework ignores a second import of a library with different arguments,
so one `Jumpstarter` import declares at most one session. Declare others with
`jumpstarter_robot.Session`.

#### Zero configuration

With `auto_import` (the default), the library connects at import and imports a
`Driver` library for every driver of the exporter, named after its full tree
path as in a generated resource (`Dut Power On`). `Acquire Lease` and
`Start Local Exporter` do the same for the session they open. With a
`selector` and no lease to attach to at import, the library only warns, and
`Acquire Lease` then connects and imports the keywords. A driver of a
second session whose prefix is already taken by another session gets the
session name in front (`Sim Dut Power On`), and
`List Driver Keywords` reports the names in use. A suite then needs no client
resource:

```robotframework
*** Settings ***
Library    jumpstarter_robot.Jumpstarter    exporter_config=${CURDIR}/exporter.yaml

*** Test Cases ***
Discover
    ${drivers}=    List Drivers
    Dut Power On
```

These keywords exist only while the suite runs, so the editor does not know
them. Generate a client resource for IntelliSense.

### `jumpstarter_robot.Session`

Declares one named session. Generated resources use it, so that one suite can
declare several sessions:

```robotframework
Library    jumpstarter_robot.Jumpstarter
Library    jumpstarter_robot.Session    android    exporter_config=${CURDIR}/exporter-android.yaml
...    AS    session.android
```

It takes the same session arguments as `Jumpstarter`: `name` (default
`default`) followed by `exporter_config`, `selector`, `exporter_name`,
`lease_name`, `config`, `duration`, `acquisition_timeout`, `allow_disabled`,
`release_grace`, `exporter_class` and `reuse_lease`, plus `on_lease_end`,
which here applies to this session only and wins over the run's policy.
Empty values (`${EMPTY}`) count as unset. `Acquire Lease` for this session
starts from these arguments.

Each session gets two keywords named after it; for the session `android`:

| Keyword | Description |
| --- | --- |
| `Connect Android Session` | Open the session now and return the exporter name; otherwise it opens when a keyword first needs it |
| `Release Android Session` | Close the session, releasing a lease it acquired; it reopens if a later keyword needs it |

### `jumpstarter_robot.Driver`

The keywords of one driver of the exporter's tree. The library reads them from
the driver's client class, statically, so libdoc and language servers list
them without a lease. At run time it binds to the driver at `path` in its
session, on the first keyword call, and checks that the driver is of the
client class the library was generated for.

```robotframework
Library    jumpstarter_robot.Driver    dut.power    client=jumpstarter_driver_power.client.PowerClient
...    prefix=Dut Power    session=default    AS    dut.power
```

| Argument | Default | Description |
| --- | --- | --- |
| `path` | `""` | Dotted path of the driver in the exporter's tree, the same as the Python access path (`dut.power` is `client.dut.power`); empty for the root |
| `client` | | Client class, as `package.module.Class` or `package.module:Class` |
| `prefix` | words of `path` | Words every keyword name starts with; an empty prefix gives bare method names |
| `session` | the suite's session | Session the driver belongs to |
| `required` | `True` | `False` marks an optional driver: `jmp robot generate --check` notes it missing instead of reporting drift |
| `methods` | | Shell driver methods (comma separated) that get keywords of their own; read from the exporter when `client` is not given |
| `role` | | Makes this a role library, which `Bind Driver` and `Use Device` point at other drivers; `path` is its default binding |
| `include` | | Only client members whose names match these glob patterns |
| `exclude` | | Skip client members whose names match these glob patterns |
| `max_items` | `1000` | Default for the `max_items` argument of keywords that read a stream |

#### Keyword names

A keyword is the prefix followed by the words of the method name, and the
generator's prefix is the driver's full tree path in words: `dut.power` with
`cycle` is `Dut Power Cycle`, and `storage` with `flash` is `Storage Flash`.
An alias (`aliases` in the project settings, or `--alias`) is the only way to
choose another prefix. It replaces every word of the path, so an alias for
`emulator.power` is written `Device Power`, which gives `Device Power On`.
Role libraries (`roles = true`) are named after the role instead, so the role
`power` gives `Power On`. Robot Framework's qualified names also work, using
the library's `AS` name: `<session>.<path>.<Keyword>`, such as
`rpi4.dutlink.power.Dutlink Power Cycle`, or `dut.power.Dut Power Cycle` for
the `default` session.

Client members become keywords as follows:

| Member | Keyword |
| --- | --- |
| method | `<Prefix> <Method Words>`, with arguments, defaults and types from its signature and documentation from its docstring |
| generator (a streaming call) | the same, returning a list of at most `max_items` items; the stream is then canceled |
| property | `Get <Prefix> <Name>` |
| context manager | none; use `Open Driver Context` |
| `async` method | none |
| shell driver | `<Prefix> Run Method` plus one keyword per configured method; positional arguments are passed to the method, named arguments become its environment, and `expected_rc` (default `0`, or `any`) checks the return code |

Arguments are converted from the method's type hints; pydantic models and
dataclasses also accept a dictionary or a JSON string. Results that are
pydantic models or dataclasses become dictionaries with attribute access
(`${result.stdout}`), and images are saved under the output directory and
returned as paths. Every keyword carries the tags `jumpstarter`,
`driver:<path>` and `class:<ClientClass>`, plus the tags of its decorator.

Drivers of some well-known classes get one more keyword, which uses the
driver's own path:

| Client class | Keyword | Same as |
| --- | --- | --- |
| `SSHWrapperClient` | `<Prefix> Connect` | `Open SSH Connection` |
| `AdbClient`, `AdbDeviceClient`, `AndroidEmulatorClient` | `<Prefix> Connect` | `Open ADB Device` |
| `IosDeviceClient` | `<Prefix> Connect` | `Open iOS Device` |
| `PySerialClient` | `<Prefix> Open Console` | `Open Console` |
| `VideoClient`, `NanoKVMUSBVideoClient` | `<Prefix> Capture Snapshot` | `Capture Video Snapshot` |

## Sessions and leases

A session is one connected driver tree. As with the Python `JumpstarterTest`
helper, a run gets a lease in one of three ways:

1. **A lease created outside the run**, such as with
   `jmp create lease -l board=rpi4 --duration 4h`, which the session
   attaches to: by name, with the
   `JMP_LEASE` environment variable or the session's `lease_name`
   (`-v JMP_LEASE_NAME:<name>`, or `JMP_<SESSION>_LEASE_NAME` for a named
   session), or automatically, when the session has a `selector` (or
   `exporter_name`), to this client's active lease with exactly the same
   selector (see [Reusing a lease](#reusing-a-lease)). This is what makes runs
   from the IDE work with no extra settings.
2. **`jmp shell`**: `jmp shell --selector board=rpi4 -- robot ...` uses the
   shell's lease for every session.
3. **`Acquire Lease`**, for example in the suite setup of an
   `__init__.robot`. It is the only keyword that acquires a lease.

A session opens the first time a keyword needs it, from the first source that
applies:

1. Inside `jmp shell`, the shell's exporter (`JUMPSTARTER_HOST`). Every session
   opened inside a shell is the shell's, whatever its name.
2. A lease name: `lease_name`, else the `JMP_LEASE` environment variable.
3. The active lease of the client config `config` that matches `selector` or
   `exporter_name`.
4. `exporter_config`: the exporter config served in this process, like
   `jmp shell --exporter-config` ({term}`local mode`). This is not a lease.

Opening on its own never acquires a lease: a session with a `selector` and no
matching lease fails, with a message that names the three ways. With
`reuse_lease` off it does not look for a matching lease, so only a lease name
or `Acquire Lease` connects it.

`Acquire Lease` connects a session the same way, but acquires a new lease for
the `selector` or `exporter_name` when there is no lease name and no matching
lease (or `reuse` is off). On a session that declares only an
`exporter_config`, it serves that config and returns no lease name, so one
suite setup works on the bench and in the lab. On a session that is already
connected, it returns that session's lease; given a new `selector`,
`exporter_name` or `lease_name`, it fails.

A keyword without a `session` argument uses the session `Switch Session`
chose, else the only session declared in the suite, else `default`. A suite
that imports one client resource therefore never names its session. A
`Switch Session` lasts until the end of the suite that called it, child suites
included.

The client config to attach to or acquire leases with is the first of:

1. the `config` argument of the import or of `Acquire Lease`;
2. the `JMP_CLIENT_CONFIG` environment variable, a client config path;
3. the current client of `jmp config client use`;
4. the client config alias `default`.

`config=env` builds the client config from `JMP_ENDPOINT`, `JMP_TOKEN` and
the other `JMP_*` environment variables.

Leases `Acquire Lease` acquired are released when the session closes: at the
end of the run, with `Release Lease`, `Stop Local Exporter` or
`Release <Name> Session`. Leases the run attached to (`lease_name`, `JMP_LEASE`,
`jmp shell`, a reused lease) are never released. A `[Timeout]` or Ctrl-C while
a session waits for its lease, or starts a local exporter, fails the test and
cancels the request. `robot --dryrun` never connects, leases or starts an
exporter.

Consoles, port forwards, Appium servers and driver contexts take a `scope`:
`test`, `suite` or `global` for the whole run. They close at the end of that
scope or before their session disconnects, whichever comes first. A `test`
scope requested in a suite setup lasts for the suite.

Each Robot Framework process holds its own leases, so under parallel runners
such as pabot, each process's suite setup acquires its own with
`Acquire Lease`. Turn lease reuse off (`reuse-lease: false` in the testbed)
or give the processes different selectors, so that they never attach to the
same lease created outside the run (see below).

### Testbeds

A testbed is a Robot Framework variable file (YAML or Python, given with
`robot -V`) whose `JUMPSTARTER` mapping configures the run's sessions:

```yaml
JUMPSTARTER:
  config: lab                  # top-level keys apply to every session
  sessions:
    rpi4:                      # keys here apply to one session and win
      selector: board=rpi4
      reuse-lease: false
```

Keys, with `-` or `_`: `selector`, `exporter-name`, `lease-name`,
`exporter-config`, `config`, `duration`, `acquisition-timeout`,
`release-grace`, `allow-disabled`, `reuse-lease` and `on-lease-end`. An
unknown key fails the import; an empty value clears a resource default.
Precedence, highest first: the session's own variable from `-v` or a variable
file (`JMP_RPI4_SELECTOR`), the testbed, the resource's defaults and the
import's arguments, then for a named session the generic `-v JMP_SELECTOR`,
`JMP_LEASE_NAME` and `JMP_CLIENT_CONFIG`. A `Jumpstarter` import without a
source takes testbed settings only for a session listed under `sessions`.
Relative paths resolve against the directory `robot` runs in.

### Reusing a lease

With `reuse_lease` (the default), a session with a `selector` or
`exporter_name`, and no `lease_name` or `JMP_LEASE`, lists the client's active
leases and attaches to one, when it opens on its own and in `Acquire Lease`
before acquiring a new lease, when:

- the client owns it, or it is shared with the client;
- its selector is equivalent to the session's: the same labels and
  expressions in any order, with `a!=x,a!=y` the same as `a notin (x,y)`. A
  narrower or wider selector never matches;
- for a session with an `exporter_name`, it is for that exporter.

Of several matches, the most recently started lease wins, then the first by
name. The run prints `Jumpstarter: reusing lease <name> for session '<session>'`
on the console. If the leases cannot be listed, it logs a warning;
`Acquire Lease` then acquires a new lease, and a session opening on its own
fails.

A reused lease is attached, so the run never releases it. Create a lease once
with `jmp create lease -l board=rpi4 --duration 4h` and every run for the same
selector, such as each run from the RobotCode buttons, attaches to it without
waiting. A lease `Acquire Lease` acquired is still released when the run
ends.

Within a run, a lease another session already holds is never reused, so
sessions with the same selector each get their own exporter. Separate
processes, such as pabot's, can still attach to the same lease created outside
Robot Framework. Give them different selectors, or turn reuse off with
`reuse-lease: false` in the testbed, `reuse_lease=False` on the `Jumpstarter`
or `Session` import, or `Acquire Lease    reuse=False`, so that each
process's `Acquire Lease` acquires its own lease.

### When a lease ends

If a lease ends, is transferred or is revoked during the run, the run stops
(`fatal`). `on_lease_end=fail` fails only the keyword instead. The
`Jumpstarter` import that gives it sets it for the whole run; imports without
it, like the one in every generated resource, leave it alone, so it can sit
next to a generated resource. Put it in the suite folder's `__init__.robot`:

```robotframework
*** Settings ***
Library    jumpstarter_robot.Jumpstarter    on_lease_end=fail
```

In the same suite file as `Resource    client.resource` it also works, in
either order, but Robot Framework warns that one of the two `Jumpstarter`
imports is ignored. When two imports give different values, the last one wins
and a warning is logged. `on_lease_end` on a `Session` import applies to that
session alone.

## Keywords

The `Jumpstarter` library provides these keywords. The tables are generated
with `python -m robot.libdoc jumpstarter_robot.Jumpstarter`, which also gives
each keyword's full documentation and examples (`libdoc ... Jumpstarter.html`).

### Sessions and leases

| Keyword | Arguments | Description |
| --- | --- | --- |
| `Acquire Lease` | `selector=None, exporter_name=None, lease_name=None, config=None, duration=None, acquisition_timeout=None, allow_disabled=False, session=None, reuse=None` | Connect the session to a lease, acquiring one when no existing lease fits. |
| `Connect To Shell Session` | `session=None` | Use the ``jmp shell`` this run was started from; returns the exporter name. |
| `Get Exporter Labels` | `session=None` | Return the labels of the session's exporter as a dictionary. |
| `Get Exporter Name` | `session=None` | Return the name of the session's exporter. |
| `Get Lease Name` | `session=None` | Return the session's lease name, or None for a local exporter. |
| `Lease Should Be Active` | `session=None` | Fail unless the session is connected and its lease has not ended. |
| `Release Lease` | `session=None` | Disconnect a session, releasing its lease if it was acquired here. |
| `Start Local Exporter` | `exporter_config, session=None` | Serve a local exporter config in this process and connect to it. |
| `Stop Local Exporter` | `session=None` | Stop a session's exporter and disconnect; it restarts if a later keyword needs it. |
| `Switch Session` | `session` | Make ``session`` the one keywords use when none is given; returns the previous one. |

`Acquire Lease` starts from the session's declaration (the `Jumpstarter` or
`Session` import that declares it) and overrides only the arguments it is
given; `reuse` defaults to the session's `reuse_lease`. It is the only keyword
that acquires a lease (see [Sessions and leases](#sessions-and-leases)). `Switch Session` lasts
until the end of the suite that calls it.

### Driver access

| Keyword | Arguments | Description |
| --- | --- | --- |
| `Bind Driver` | `library, path` | Point the Driver library imported ``AS library`` at the driver at ``path``. |
| `Call Driver` | `path, method, *args, session=None, **kwargs` | Call any client method by driver path, including ones without a keyword. |
| `Get Driver` | `path, session=None` | Return the Python client of a driver, for ``Call Method`` and Python keywords. |
| `List Driver Keywords` | `path="", session=None` | Return the keyword names of the driver at ``path``, as its imported Driver library names them. |
| `List Drivers` | `session=None` | List the session's drivers with their client classes, and log them as a table. |
| `Use Device` | `device, session=None` | Bind every role library to the matching driver under ``device``. |

### Consoles

Consoles read and write any driver byte stream like a terminal: a serial
port, a network connection, a QEMU console. Several can be open; `Open
Console` makes the new one current. Everything a console receives is also
written to `${OUTPUT DIR}/jumpstarter/console-<alias>-<n>.log`.

| Keyword | Arguments | Description |
| --- | --- | --- |
| `Clear Console Buffer` |  | Discard the output received and not yet read; the transcript keeps it. |
| `Close All Consoles` |  | Close every open console; indices start from 1 again. |
| `Close Console` | `alias=None` | Close the current console, or the one with index or ``alias``. |
| `Console Execute Command` | `command, timeout=None, strip_prompt=True` | Write ``command`` and a newline, read until the prompt and return the command's output. |
| `Console Expect` | `pattern, timeout=None` | Read until regular expression ``pattern`` matches; returns the match and its groups. |
| `Console Login` | `username, password, login_prompt=login:, password_prompt=Password:, login_incorrect=Login incorrect, timeout=None` | Log in on the console; returns the output read. The password is never logged. |
| `Console Read` | `delay=0.1s` | Wait ``delay`` for output and return everything received and not yet read. |
| `Console Read Until` | `expected, timeout=None` | Read until ``expected`` appears; returns the output up to and including it. |
| `Console Read Until Prompt` | `strip_prompt=False, timeout=None` | Read until the console's prompt; returns the output, with the prompt unless ``strip_prompt``. |
| `Console Read Until Regexp` | `*patterns, timeout=None` | Read until one of the regular expressions ``patterns`` matches; returns the output up to the match's end. |
| `Console Should Contain` | `text, timeout=None` | Wait for ``text`` to appear on the console; fails if it does not within ``timeout``. |
| `Console Should Not Contain` | `text, duration=5s` | Watch the console for ``duration``; fails as soon as ``text`` appears. |
| `Console Write` | `text` | Write ``text`` to the current console, without a newline. |
| `Console Write Control Character` | `character` | Write a control character to the current console. |
| `Console Write Line` | `text=""` | Write ``text`` and the console's newline to the current console. |
| `Get Console Transcript` | `alias=None, as_file=False` | Return everything the current console (or ``alias``) received, or its transcript file with ``as_file``. |
| `Open Console` | `path, alias=None, method=connect, timeout=10s, newline=LF, prompt=None, prompt_is_regexp=False, encoding=UTF-8, encoding_errors=replace, loglevel=INFO, scope=suite, session=None` | Open a console on the byte stream of the driver at ``path`` and make it current. |
| `Set Console Newline` | `newline` | Set what `Console Write Line` appends (``LF``, ``CRLF``, ``CR`` or a literal); returns the previous one. |
| `Set Console Prompt` | `prompt, prompt_is_regexp=False` | Set the current console's prompt; returns the previous ``(prompt, prompt_is_regexp)``. |
| `Set Console Timeout` | `timeout` | Set the current console's default read timeout; returns the previous one. |
| `Switch Console` | `index_or_alias` | Make the console with ``index_or_alias`` current; returns the index of the previous one. |

### Driver contexts, streams and properties

| Keyword | Arguments | Description |
| --- | --- | --- |
| `Close All Driver Contexts` |  | Exit every open driver context, most recently opened first. |
| `Close Driver Context` | `alias=None` | Exit an open driver context; without ``alias``, the most recently opened one. |
| `Collect Driver Stream` | `path, method, *args, count=None, timeout=10s, driver_method=None, session=None` | Read a streaming driver call for ``count`` items or ``timeout``, then cancel it. |
| `Get Driver Context Value` | `alias=None` | Return what an open driver context yielded, as `Open Driver Context` returned it. |
| `Get Driver Property` | `path, name, session=None` | Return a property of the driver client at ``path``. |
| `Open Driver Context` | `path, method, *args, alias=None, scope=suite, session=None, **kwargs` | Enter a client method that is a context manager and keep it open. |
| `Open Port Forward` | `path, local_port=0, method=connect, alias=None, scope=suite, session=None` | Forward a local TCP port to a driver's byte stream; returns ``host:port``. |

### Video

| Keyword | Arguments | Description |
| --- | --- | --- |
| `Capture Video Snapshot` | `path, filename=None, embed=True, session=None` | Save one frame from the video driver at ``path`` and return the file path. |

### Appium

`Start Appium` serves a leased target's Appium on a loopback HTTPS port.
Import `AppiumLibrary` in the suite and open the session with its
`Open Application`:

```robotframework
${appium}=    Start Appium    emulator.appium
Open Application    ${appium.url}    &{appium.capabilities}    appium:appPackage=com.android.settings
```

`Start Appium` and `Start Device Hub Appium` return a dictionary: `url`,
`capabilities` (the ones the target fixes: its platform, automation name and
device), `ca_file` (the lease's CA certificate, for other WebDriver clients),
`wda_url` (Device Hub only), `alias`, `path` and `session`. Pass an
`Upload App` token to `Open Application` as `appium:app`; with Device Hub,
give `Upload App` the device's driver path or the server's alias, and sessions
otherwise behave as on the exporter. `Stop Appium` does not close
applications; close them first with `Close All Applications`.

| Keyword | Arguments | Description |
| --- | --- | --- |
| `Start Appium` | `path=appium, port=0, alias=None, scope=global, session=None` | Serve the Appium of the driver at ``path`` on a loopback HTTPS port; returns its URL and capabilities. |
| `Start Device Hub Appium` | `path, xctestrun, appium=appium, alias=None, scope=global, session=None` | Show an iOS device in Device Hub, run WebDriverAgent and Appium on this Mac; returns the same as `Start Appium`. |
| `Stop Appium` | `alias=None` | Stop serving an Appium server; close its applications first (``Close All Applications``). |
| `Upload App` | `path, file, session=None` | Upload an application for the Appium at ``path``; returns its ``jumpstarter-app:`` token. |

### SSH

`Open SSH Connection` forwards a local port to an `SSHWrapper` driver, as
`j ssh` does, and opens an SSHLibrary connection over it, so every SSHLibrary
keyword works on the leased device. Import `SSHLibrary` in the suite.
`Execute Over SSH` needs only a local `ssh` binary.

| Keyword | Arguments | Description |
| --- | --- | --- |
| `Close SSH Connection` | `alias=None` | Close an SSH connection opened by `Open SSH Connection`, then its forward. |
| `Execute Over SSH` | `command, path=ssh, expected_rc=0, session=None` | Run ``command`` on the device with the local ``ssh`` binary, like ``j ssh``. |
| `Get SSH Endpoint` | `path=ssh, scope=suite, session=None` | Forward a local port to the SSH driver at ``path`` and return how to log in. |
| `Open SSH Connection` | `path=ssh, alias=None, password=None, username=None, scope=suite, session=None, **sshlibrary_options` | Open an SSHLibrary connection to the device behind the SSH driver at ``path``. |

### ADB

| Keyword | Arguments | Description |
| --- | --- | --- |
| `ADB Get Property` | `name` | Return the system property ``name`` (``getprop``); empty when unset. |
| `ADB Install` | `apk, replace=True, grant_permissions=True` | Install the APK file ``apk`` on the device. |
| `ADB Logcat` | `lines=200, clear=False` | Return the last ``lines`` lines of the device log, and log them. |
| `ADB Pull` | `remote, local=None` | Copy ``remote`` from the device to ``local``; returns the local path. |
| `ADB Push` | `local, remote` | Copy the local file ``local`` to ``remote`` on the device. |
| `ADB Reboot` | `wait=False, timeout=180s` | Reboot the current device; with ``wait``, wait until it has booted again. |
| `ADB Screenshot` | `filename=None` | Take a screenshot, save it as PNG and embed it in the log; returns its path. |
| `ADB Shell` | `command, timeout=None, expected_rc=0` | Run ``command`` in the device's shell and return its output. |
| `ADB Uninstall` | `package` | Uninstall ``package`` from the device; fails if it is not installed. |
| `ADB Wait For Boot` | `timeout=180s` | Wait until the current device reports ``sys.boot_completed`` as ``1``. |
| `Close ADB Device` | `alias=None` | Close the current ADB device, or the one with ``alias``, and its forward. |
| `Close All ADB Devices` |  | Close every ADB device opened by `Open ADB Device`, for a suite teardown. |
| `Get ADB Environment` | `path, scope=suite, session=None` | Forward the ADB driver at ``path`` and return the environment for your own tools. |
| `Open ADB Device` | `path, alias=None, serial=None, timeout=180s, wait_for_boot=True, scope=suite, session=None` | Open the Android device behind the ADB driver at ``path`` and make it current. |
| `Switch ADB Device` | `alias` | Make the ADB device with ``alias`` (or index) current; returns the previous index. |

### iOS

`Open iOS Device` serves the device's transport (usbmux, or idb for
simulators) on a loopback port; `Run iOS Tool` runs `idevice*`, `go-ios`,
`pymobiledevice3` or `idb` against it. The `iOS ...` app keywords need `idb`
on `PATH`.

| Keyword | Arguments | Description |
| --- | --- | --- |
| `Close iOS Device` | `alias=None` | Stop serving the current iOS device, or the one with ``alias``, and its forwards. |
| `Get iOS Environment` | `alias=None` | Return the environment that points iOS tools at the device. |
| `Get iOS HTTPS Endpoint` | `alias=None` | Serve the device's HTTPS service locally; returns ``url``, ``ca_file`` and ``metadata``. |
| `iOS Device Info` | `alias=None` | Return the device's identity, presence, protocols and forward ports. |
| `iOS Forward Port` | `port, local_port=None, alias=None` | Forward the device's TCP ``port`` to loopback; returns ``host:port``. |
| `iOS Install App` | `app, alias=None` | Install the ``.app`` or ``.ipa`` at ``app`` with ``idb install``. |
| `iOS Launch App` | `bundle_id, *args, alias=None` | Launch the app ``bundle_id`` with ``idb launch``; ``args`` go to the app. |
| `iOS Screenshot` | `filename=None, alias=None` | Take a screenshot with ``idb screenshot``, embed it in the log; returns its path. |
| `iOS Syslog` | `duration=5s, alias=None` | Collect the device log with ``idb log`` for ``duration``; returns and logs it. |
| `iOS Terminate App` | `bundle_id, alias=None` | Stop the app ``bundle_id`` with ``idb terminate``. |
| `Open iOS Device` | `path=ios, protocol=None, alias=None, scope=suite, session=None` | Serve the transport of the iOS device at ``path`` on a loopback port and make it current. |
| `Run iOS Tool` | `command, *args, expected_rc=0, timeout=None, alias=None` | Run a local iOS tool against the device and return its result. |
| `Switch iOS Device` | `alias` | Make the iOS device with ``alias`` (or index) current; returns the previous index. |

### Device connection

| Keyword | Arguments | Description |
| --- | --- | --- |
| `Connect To Device` | `path, alias=None, session=None, **options` | Connect to the device at ``path`` over SSH, ADB or iOS, whichever its driver offers. |

## Generating client resources

`jmp robot generate` writes a client resource: a `.resource` file that
imports `Jumpstarter`, declares a session with `Session` and imports a
`Driver` library per driver. The resource is deterministic, so it can be
committed, and it holds no credentials.

`jmp robot` is available once `jumpstarter-robot` is installed alongside the
Jumpstarter CLI; `jmp-robot` is the same command from `jumpstarter-robot`
alone.

```console
$ jmp robot generate                          # every client of robot.toml
$ jmp robot generate --client rpi4            # one client
$ jmp robot generate --exporter-config exporter.yaml -o client.resource
$ jmp robot generate --capture --client rpi4  # record its drivers from a live exporter
$ jmp robot generate --check                  # compare recorded drivers with live exporters
```

With no source on the command line, it regenerates every client of the
nearest project file. Each client's tree comes from the first of its `nodes`
table, its `exporter-config`, its `tree` or its `exporter-class`. A client
with only a `selector` or `lease` needs `--capture` or `--live`.

| Option | Description |
| --- | --- |
| `--project FILE` | Project file to read (default: the nearest `robot.toml` or `pyproject.toml` with client settings) |
| `--client NAME` | Generate only this client; repeatable |
| `--capture` | Record the client's drivers from a live exporter into its `nodes` table, then generate |
| `--check` | Compare the committed `nodes` with a live exporter; exit 1 on drift |
| `--live` | Generate from a live exporter without writing `nodes` |
| `--from FILE` | Use a `jmp describe lease --drivers -o json` dump instead of a live exporter |
| `--exporter-config CONFIG` | Exporter config, by path or alias |
| `--selector SELECTOR` | Lease by label selector |
| `--lease NAME` | Attach to an existing lease |
| `--tree FILE` | Device tree JSON, such as one produced by other tooling |
| `--exporter-class FILE[#NAME]` | ExporterClass YAML |
| `--driver-interface-dir DIR` | Directory of DriverInterface YAML for `--exporter-class`; repeatable |
| `--config CONFIG` | Client config to lease with: alias, path or `env` |
| `-o`, `--output FILE` | Resource to write |
| `--session NAME` | Session name (default: the client name, or `default`); also applies to the clients of the project file |
| `--alias PATH=PREFIX` | Keyword prefix for a driver; repeatable |
| `--roles` | Role libraries for identical subtrees |
| `--include GLOB`, `--exclude GLOB` | Only, or not, drivers whose path matches; repeatable |
| `--libspec DIR` | Also write a libspec per driver library, for tools that read libspec files |

Live sources (`--capture`, `--check`, `--live`) use the `jmp shell` the
command runs in when there is one, else lease the client's `selector` or
`lease` with its `config`, else serve its `exporter-config`. A shell has one
exporter, so inside `jmp shell`, without `--client` or `--from`, they pick one
client: the project's only client; with several, the one whose `lease` is the
shell's `JMP_LEASE` or whose `selector` matches its `JMP_EXPORTER_LABELS`,
failing when none or more than one matches (always so in a local
`jmp shell --exporter-config` shell, which sets neither); with no clients,
`--capture` and `--live` use a new `default` client writing `client.resource`
(`--capture` also adds it to `robot.toml`), and `--check` fails. Inside a
shell, `--client` names one client, not several. When the recorded client has
no `selector`, `lease` or `exporter-config`, `--capture` notes that it
connects only inside `jmp shell`. A `--from` dump lists no shell methods:
`--check --from` does not compare them, and `--capture --from` keeps the
committed `methods` of drivers whose class did not change.

When two drivers would get keywords of the same name after Robot Framework's
normalization, such as `a_b.c` and `a.b_c` (both `A B C`), or an alias clashes,
the generator fails, names the drivers and asks you to alias one of them.
`jmp robot render` reads a device tree JSON on stdin and writes a resource to
stdout, for other code generators. It takes `--session NAME` (default
`default`) and `--roles`.

`--capture`, `--check` and `--live` cannot be combined, and neither can
`--exporter-config`, `--selector`, `--lease`, `--tree` and `--exporter-class`.

Exit codes: `0` success, `1` drift (`--check`), `2` usage or configuration
error.

### The generated resource

```robotframework
*** Settings ***
Documentation    Generated by `jmp robot generate` from the committed nodes table robot.toml [tool.jumpstarter.client.rpi4.nodes] (device Rpi4, session rpi4). Do not edit: regenerating overwrites it.
Library    jumpstarter_robot.Jumpstarter    auto_import=False
Library    jumpstarter_robot.Session    rpi4    exporter_config=${JMP_RPI4_EXPORTER_CONFIG}    selector=${JMP_RPI4_SELECTOR}    lease_name=${JMP_RPI4_LEASE_NAME}    config=${JMP_RPI4_CLIENT_CONFIG}    AS    session.rpi4
Library    jumpstarter_robot.Driver    dutlink.power    client=jumpstarter_driver_power.client.PowerClient    prefix=Main Power    session=rpi4    AS    rpi4.dutlink.power

*** Variables ***
${JMP_RPI4_EXPORTER_CONFIG}    ${EMPTY}
${JMP_RPI4_SELECTOR}    board=rpi4
${JMP_RPI4_LEASE_NAME}    ${EMPTY}
${JMP_RPI4_CLIENT_CONFIG}    lab
```

How the session connects is a variable default, so `robot -v
JMP_RPI4_SELECTOR:board=rpi5`, `-v JMP_RPI4_LEASE_NAME:<lease>` or a pabot
argument file can point the same resource at another exporter. The `default`
session uses `${JMP_EXPORTER_CONFIG}`, `${JMP_SELECTOR}`, `${JMP_LEASE_NAME}`
and `${JMP_CLIENT_CONFIG}`. A named session gets its own names and never
defines the `default` session's, so client resources of several sessions,
the `default` one included, can be imported into one suite. A named session
with no selector, lease name or client config of its own takes
`-v JMP_SELECTOR`, `-v JMP_LEASE_NAME` and `-v JMP_CLIENT_CONFIG` from the
command line (or a `-V` variable file or `Set Global Variable`, never from
another resource). Paths are written
relative to the resource (`${CURDIR}/exporter.yaml`). With roles, a list
variable names the devices `Use Device` accepts: `@{DEVICES}` for the
`default` session, `@{<SESSION>_DEVICES}` for a named one, such as
`@{RPI4_DEVICES}`. `AS` names are the driver paths, prefixed with the session
name for sessions other than `default`.

## Project settings

The generator reads `[tool.jumpstarter.client]` from `robot.toml`, the file
RobotCode and `robot.toml`-aware tools read, or from `pyproject.toml`. In each
directory from the current one upwards, a `robot.toml` with client settings
wins, then a `pyproject.toml` with them, then a bare `robot.toml`.

```toml
[tool.jumpstarter.client]          # defaults for every client below
config = "lab"                     # client config alias, path or "env"
roles = true

[tool.jumpstarter.client.rpi4]     # one client; its name is also the session name
selector = "board=rpi4"
resource = "client-rpi4.resource"

[tool.jumpstarter.client.rpi4.aliases]   # tree path = keyword prefix
"dutlink.power" = "Main Power"

[tool.jumpstarter.client.rpi4.nodes]     # tree path = client class
"dutlink.power"   = "jumpstarter_driver_power.client.PowerClient"
"dutlink.storage" = "jumpstarter_driver_opendal.client.StorageMuxFlasherClient"
"camera"          = { client = "jumpstarter_driver_ustreamer.client.UStreamerClient", required = false }
"shell"           = { client = "jumpstarter_driver_shell.client.ShellClient", methods = ["build", "deploy"] }
```

Keys of `[tool.jumpstarter.client]`, which apply to every client:

| Key | Description |
| --- | --- |
| `config` | Client config: alias, path or `env` |
| `roles` | `true` to generate role libraries for identical subtrees |
| `include` | Glob, or list of globs, of driver paths to generate |
| `exclude` | Glob, or list of globs, of driver paths to skip |

Keys of `[tool.jumpstarter.client.<name>]`:

| Key | Description |
| --- | --- |
| `exporter-config` | Exporter config, by path or alias; the resource then runs in {term}`local mode` by default |
| `selector` | Label selector of the lease to attach to at run time, or to acquire with `Acquire Lease` |
| `lease` | Existing lease to attach to |
| `exporter-class` | ExporterClass YAML (`FILE` or `FILE#name`) to generate from |
| `driver-interface-dir` | Directory, or list of directories, of DriverInterface YAML for `exporter-class` |
| `tree` | Device tree JSON to generate from |
| `config` | Client config: alias, path or `env`; overrides the default |
| `resource` | Resource to write (default `client-<name>.resource`) |
| `aliases` | Table of tree path (or role) to keyword prefix |
| `nodes` | Table of tree path to client class: the committed driver mapping |
| `include`, `exclude`, `roles` | As above, for this client |

A `nodes` value is a client class path, or an inline table with `client`,
`required` (`false` for optional drivers), `methods` (shell methods) and
`interface`. Parents that are not listed are implied. When `nodes` is present
it is what the resource is generated from; `exporter-config`, `selector` and
`lease` then only say how to connect. Next to an `exporter-class`, `nodes`
refines the class instead: its entries set the client class, `methods` and
`required` of the class's drivers, and add drivers the class does not
declare.

Paths are relative to the project file; values without a path separator or a
`.yaml` suffix are aliases. Setting names cannot name a client, and keys that
look like credentials (`token`, `password`, `secret`) are refused. The
generator warns when a committed client config file contains a token.

## Curating keywords in drivers

Every public method of a `DriverClient` subclass is a keyword without changes
to the driver, whichever package the client comes from. Driver authors curate
them with `jumpstarter.client.decorators.robot`, which mirrors Robot
Framework's `robot.api.deco` without making the driver depend on Robot
Framework:

```python
from jumpstarter.client import DriverClient
from jumpstarter.client.decorators import robot


class PowerClient(DriverClient):
    @robot.keyword("Should Be On", tags=["assert"])
    def should_be_on(self) -> None:
        """Fail unless the driver reports the power is on."""

    @robot.not_keyword
    def raw_status(self) -> str: ...
```

| Decorator | Effect |
| --- | --- |
| `@robot.keyword(name=None, tags=(), types=(), doc=None)` | Name, tag or document a keyword; `name` replaces the method words and the driver's prefix is still added (`Dut Power Should Be On` on `dut.power`); `types` overrides argument types, or `None` turns conversion off; works bare too |
| `@robot.not_keyword` | Keep a public method out of Robot Framework |
| `@robot.library(auto_keywords=False, doc=None)` | On the client class: expose only `@robot.keyword` methods, as in Robot Framework; `doc` replaces the class docstring as the library introduction |

The decorators set the attributes Robot Framework's own decorators set, so a
client that already depends on Robot Framework can use `robot.api.deco`
instead.

## IntelliSense with RobotCode

[RobotCode](https://robotcode.io/) reads keywords from the libraries a suite
imports, so a generated client resource gives completion and documentation for
every driver keyword without a lease:

1. Select the Python interpreter of the environment with `jumpstarter-robot`
   and the driver packages (**Python: Select Interpreter**).
2. Import the generated resource in the suites (`Resource    client.resource`).
3. Keep `paths`, `python-path` and `output-dir` in `robot.toml`, next to the
   `[tool.jumpstarter.client]` settings.
4. After regenerating, run **RobotCode: Clear Cache and Restart Language
   Servers**.

To run tests from the editor on a leased board, create the lease once, with
`jmp create lease -l board=rpi4 --duration 4h`, and give the session the same
selector, in the client's `selector` or in RobotCode's `[variables]`
(`JMP_RPI4_SELECTOR = "board=rpi4"`). Each run then attaches to that lease
and leaves it active for the next (see [Reusing a lease](#reusing-a-lease)).

Other tools that read libspec files can use
`jmp robot generate --libspec DIR`.

## Limitations

- Keywords come from the installed client classes. A driver whose client is
  not installed, or not allowed by the client config's `drivers.allow`, is
  skipped by the generator and fails when used.
- An exporter config lists only the drivers it configures. Children that a
  driver builds itself are missing from a resource generated from it; record
  them from a live exporter with `--capture` (or `--live`).
- `async` client methods get no keyword, and context managers are used through
  `Open Driver Context`. Use `Call Driver` for any other method.
- `Call Driver` and `Open Driver Context` convert text arguments to the client
  method's annotated types. `Collect Driver Stream` calls a driver export by
  name, so it passes arguments unconverted (write numbers as `${5}`) and returns
  the values the driver sent (dictionaries rather than the client's model
  classes).
- Inside `jmp shell`, every session is the shell's exporter.
- A console keeps up to 4 Mi characters of unread output and then drops the
  oldest, with a warning; its transcript file keeps everything. Regular
  expression reads may miss a single match longer than 64 KiB.
- Zero configuration keywords exist only once the run has connected, and
  `robot --dryrun` never connects, so a dry run reports them as missing.
  Suites with a generated client resource dry-run cleanly.
- Leasing by ExporterClass (`exporter_class`) is not supported yet; the
  generator can already read an ExporterClass.
- Keywords hold a lock while they talk to an exporter, so keywords run from
  several threads run one at a time.
