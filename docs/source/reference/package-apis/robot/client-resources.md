# Client resources

A client resource is a
[resource file](https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#resource-files)
that imports the Jumpstarter libraries, says how to connect to the exporter,
and imports one keyword library per driver. `jmp robot generate` writes it;
commit it next to your tests and regenerate it when the exporter's drivers
change. The output is deterministic and contains no credentials.

`jmp robot` is available once `jumpstarter-robot` is installed alongside the
Jumpstarter CLI; `jmp-robot` is the same command from `jumpstarter-robot`
alone.

The generator reads `[tool.jumpstarter.client]` in `robot.toml` (or
`pyproject.toml`). Each sub-table is one client, and its name is the session
name unless `--session NAME` overrides it. With no arguments, `generate`
regenerates every client in the nearest project file; `--client NAME`
regenerates one. "Project settings" in the {doc}`reference <reference>` lists
every key.

## From the exporter config

If you have the exporter config (local mode, or an exporter you administer), set
`exporter-config` to its path or to an alias from `jmp config exporter list`.
The generator reads the driver classes without starting any driver, and the
resource serves that config in local mode by default:

```toml
[tool.jumpstarter.client.board]
exporter-config = "exporter.yaml"
resource = "client.resource"
```

An exporter config lists only the drivers it configures. Drivers that create
their own children, such as a Dutlink board's power and storage, need a live
source: record them with `--capture`.

## Without the exporter config: a committed driver mapping

Most test authors never see the exporter config, because lab administrators
own it. The generator can instead read a table that maps tree paths to client
classes, committed next to the tests. Someone with access records it once;
everyone else generates from it, with no exporter config and no lease:

```toml
[tool.jumpstarter.client.rpi4]
selector = "board=rpi4"                 # the lease to attach to, or to acquire with Acquire Lease
resource = "client-rpi4.resource"

[tool.jumpstarter.client.rpi4.nodes]
"dutlink.power"   = "jumpstarter_driver_power.client.PowerClient"
"dutlink.storage" = "jumpstarter_driver_opendal.client.StorageMuxFlasherClient"
"dutlink.console" = "jumpstarter_driver_pyserial.client.PySerialClient"
"camera"          = { client = "jumpstarter_driver_ustreamer.client.UStreamerClient", required = false }
"shell"           = { client = "jumpstarter_driver_shell.client.ShellClient", methods = ["build", "deploy"] }
```

`required = false` marks a driver some exporters lack. `methods` lists a shell
driver's configured methods, each of which gets its own keyword. Write the
table by hand, or record it from a live exporter:

```console
$ jmp robot generate --capture --client rpi4
rpi4: recorded 5 nodes in robot.toml
rpi4: wrote client-rpi4.resource (5 driver libraries)
```

`--capture` uses the `jmp shell` it runs in, else leases the client's
`selector` or `lease`, else serves its `exporter-config`. It writes the `nodes` table into `robot.toml`, preserving comments, and
regenerates the resource. `--from describe.json` records the table from a
`jmp describe lease <name> --drivers -o json` dump instead. A dump lists no
shell methods, so `--from` keeps the committed `methods` of every driver whose
class is unchanged.

To detect exporters that no longer match the mapping, run `--check` in CI. It
compares the committed `nodes` with the live exporter and exits with code 1
on drift: a missing required driver, a changed class, or new drivers. With
`--from`, shell methods are not compared.

```console
$ jmp robot generate --check --client rpi4
rpi4: the committed nodes match the live exporter
```

Each driver library also checks the driver it binds to at run time, so a stale
resource fails with an instruction to regenerate it.

## From a live `jmp shell`

Inside `jmp shell`, `--capture` records the shell's exporter, so you can
generate a resource for any exporter you can lease:

```console
$ jmp shell --selector board=rpi4 -- jmp robot generate --capture
```

The shell has one exporter, and without `--client` the command works out
which client it is from what a lease shell reports (`JMP_LEASE` and
`JMP_EXPORTER_LABELS`):

- with one client, that client, whatever its source;
- with several, the one whose `lease` is the shell's lease or whose `selector`
  matches the exporter's labels;
- with none (or no project file), a new `default` client (or the
  `--session` name) in the project file, or in a new `robot.toml` in the
  current directory, and `client.resource`. It has no selector or exporter
  config, so the command suggests adding a `selector` to its table or passing
  `-v JMP_SELECTOR:...`; a run then attaches to a matching lease, or
  `Acquire Lease` acquires one.

With several clients, when none matches or more than one does, the command
fails and lists them: name the client with `--client`. A local
`jmp shell --exporter-config` shell reports no lease or labels, so in a
project with several clients it always needs `--client`. Inside a shell,
`--client` names one client, not several.

`--check` and `--live` choose the client the same way, except in a project
with no clients: `--live` writes `client.resource` from the shell's exporter
without adding a client to `robot.toml`, and `--check` fails, as there are no
committed nodes to compare. `--live` generates from the live exporter without
recording anything.

## Keyword names and aliases

A driver's keywords are its full tree path followed by the method name, in
words: `dutlink.power` with `on` is `Dutlink Power On`, and `storage` with
`flash` is `Storage Flash` (see the
[guide](../../../getting-started/guides/robot-framework.md#keyword-names)).
Aliases are the only way to choose another prefix:

```toml
[tool.jumpstarter.client.rpi4.aliases]
"dutlink.power" = "Main Power"
"dut" = "Board"
```

An alias replaces the whole path: `"dutlink.power" = "Main Power"` gives
`Main Power On`, and `--alias "dutlink.power=Main Power"` does the same on the
command line. If two drivers would produce keywords with the same name, for
example `a_b.c` and `a.b_c`, which Robot Framework both reads as `A B C`, the
generator stops, names both and asks you to alias one of them.

[Qualified names](https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#handling-keywords-with-same-names)
use the library's
[`AS` name](https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#setting-custom-name-to-library),
`<session>.<path>.<Keyword>`, such as `board.dut.power.Dut Power Cycle`; the
`default` session omits the session part (`dut.power.Dut Power Cycle`).

## Sharing resources as a Python package

Robot Framework finds resource files on the Python module search path, so a
lab can publish its client resources as a Python package, for example
`acme_lab_clients` containing `rpi4.resource`. Generate the resources from
committed `nodes` tables so they carry selectors, not exporter config paths.
Test authors install the package and import the resource by package path:

```robotframework
*** Settings ***
Resource    acme_lab_clients/rpi4.resource
```

They get the keywords and IntelliSense without a mapping or exporter config
of their own.

## Zero configuration

Without a client resource, importing the Jumpstarter library with a source
connects at import and imports a keyword library for every driver it finds.
With a selector it attaches to a matching lease; when there is none it only
warns, and `Acquire Lease` then connects and imports the keywords:

```robotframework
*** Settings ***
Library    jumpstarter_robot.Jumpstarter    exporter_config=${CURDIR}/exporter.yaml

*** Test Cases ***
Discover
    ${drivers}=    List Drivers
    Dut Power On
```

The auto-imported keywords carry the same full-path names as a generated
resource. This is convenient for exploring an exporter, but the editor cannot see these
keywords before the run, and a
[dry run](https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#dry-run),
which never connects, reports them as missing (the library warns about this).
Generate a client resource for tests you keep. The
`python/examples/robot/zero-config` example drives the basic example's board
this way, and reaches its drivers by path with `Call Driver`,
`Open Driver Context`, `Collect Driver Stream` and `Get Driver Property`.

## Outlook: ExporterClass

A client resource describes one exporter's drivers, taken from its config, a
committed mapping or a live lease, and runs on any exporter with the same
drivers. The generator can already read an ExporterClass and its
DriverInterfaces (`exporter-class` in `robot.toml`); a `nodes` table next to
it names client classes, shell methods and optional drivers, and adds drivers
the class does not declare. Once Jumpstarter leases by ExporterClass, a
resource generated from one will run on any exporter of that class, and the
committed mapping becomes optional.
