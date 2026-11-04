# Sessions and leases

A session is one connected exporter. Each client in `robot.toml` is a session
named after its table, so a run can drive a board and a simulator, or an
Android emulator and an iOS simulator.

## Testbeds

A testbed is a Robot Framework
[variable file](https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#variable-files),
in [YAML](https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#variable-file-as-yaml)
or Python, that describes one place the tests run: which exporters each session
uses, and the test data that goes with them. Keep one per lab, board type or CI
pool, and choose it per run with `-V`; the suites and client resources never
change. This is the recommended way to scale a Robot Framework project over
many labs.

```yaml
# python/examples/robot/lab/testbeds/lab.yaml
JUMPSTARTER:
  config: lab
  selector: board=mockdut,lab=eu
  duration: 1 hour
PASSWORD: change-me
```

```console
$ robot -d results -V testbeds/lab.yaml tests/
```

A suite that imports several client resources can give each session its own
settings under `sessions`, for example:

```yaml
JUMPSTARTER:
  config: lab                  # shared by every session
  duration: 1 hour
  sessions:
    board:
      selector: board=mockdut,lab=eu
    pair:
      selector: board=mockdut-pair,lab=eu
```

`JUMPSTARTER` takes these keys, written with `-` or `_`, either at its top
level, where they apply to every session, or under `sessions.<name>`, where
they apply to one session and win over the shared ones:

| Key | Meaning |
| --- | --- |
| `selector`, `exporter-name`, `lease-name` | The lease to attach to, by label selector, by exporter or by name; `Acquire Lease` acquires by selector or exporter |
| `exporter-config` | Serve a local exporter config (a path relative to the directory `robot` runs in, or an alias) |
| `config` | Client config: an alias, a client config YAML path, or `env` |
| `duration`, `acquisition-timeout`, `release-grace` | Lease timing, as Robot Framework times (`1 hour`, `30 s`) |
| `allow-disabled`, `reuse-lease` | Lease a disabled exporter; attach to a matching existing lease (off: only a named lease or `Acquire Lease` connects) |
| `on-lease-end` | `fatal` or `fail` for the session |

An empty value clears a resource default; for example `selector: ""` makes a
session whose resource sets a selector serve its exporter config instead.

Settings apply in this order, highest first:

1. the session's own variables given with `-v` or in a variable file, such as
   `JMP_BOARD_SELECTOR`;
2. the testbed's `JUMPSTARTER` settings;
3. the generated resource's defaults and the library import's arguments;
4. for a named session, the generic `-v JMP_SELECTOR`, `JMP_LEASE_NAME` and
   `JMP_CLIENT_CONFIG`.

A bare `Library    jumpstarter_robot.Jumpstarter` import, such as the one in
every generated resource, takes testbed settings only for a session listed
under `sessions`. Everything else in the file is ordinary test data:
`${PASSWORD}` above overrides a `${PASSWORD}` default in a suite. Keep secrets
out of committed testbeds; pass them with `-v`, or use a Python variable file
that reads them from the environment.

[RobotCode](https://robotcode.io/) profiles in `robot.toml` select a testbed
from the editor, and `robotcode --profile lab robot` does the same on the
command line:

```toml
[profiles.mock]
description = "The mock board, served in-process (local mode)"
variable-files = ["testbeds/mock.yaml"]

[profiles.lab]
description = "A board from the lab controller: your lease for the selector, else a new one"
variable-files = ["testbeds/lab.yaml"]

[profiles.lab-pool]
description = "One new lease per process from a pool, for pabot"
variable-files = ["testbeds/lab-pool.yaml"]
```

The `python/examples/robot/lab` example runs the same suites with the mock,
lab and pool testbeds in its `testbeds/` folder; run the commands above from
that folder. Its `tests/__init__.robot` connects the board for every suite
with `Suite Setup    Acquire Lease    session=board`: with `lab.yaml` that
attaches to a matching lease or acquires one, with `lab-pool.yaml` it always
acquires one, and with `mock.yaml` it serves the mock board in-process.

## Several sessions

A suite that imports one client resource uses that session implicitly.
Jumpstarter keywords take a `session` argument to choose another, and
`Switch Session` changes the default until the end of the calling suite,
including its child suites:

```robotframework
${status}=    Call Driver    dut.power    status    session=board
Switch Session    pair
Use Device    dut2
```

Each session also gets `Connect <Name> Session` and `Release <Name> Session`
keywords, such as `Connect Board Session`. `Connect <Name> Session` opens the
session now, as its first keyword would; like it, it never acquires a lease.

Robot Framework keeps the first value of a variable, so each session's
resource uses its own variable names: `${JMP_EXPORTER_CONFIG}`,
`${JMP_SELECTOR}`, `${JMP_LEASE_NAME}` and `${JMP_CLIENT_CONFIG}` for the
`default` session, and `${JMP_<SESSION>_EXPORTER_CONFIG}`,
`${JMP_<SESSION>_SELECTOR}` and so on for named ones (`${JMP_PAIR_SELECTOR}`
for session `pair`). A suite can therefore import several client resources,
including the `default` session's, and each session keeps its own exporter.

## Several identical devices

Identical boards each get keywords named after their own path, such as
`Dut1 Power On` and `Dut2 Power On`. With `roles = true`, the generator
instead imports one *role* library per driver role, whose keywords are named
after the role rather than the board (the role `power` gives `Power On`), plus
a device list named after the session: `@{DEVICES}` for the `default`
session, `@{<SESSION>_DEVICES}` for others (such as `@{MULTI_DEVICES}`).
`Use Device` points every role library at one device, so one set of keywords
drives each board in turn:

```robotframework
*** Settings ***
Resource    ../client.resource

*** Test Cases ***
Every DUT Boots
    FOR    ${dut}    IN    @{MULTI_DEVICES}
        Use Device    ${dut}
        Power Off
        Open Console    ${dut}.console    alias=${dut}
        Power On
        Console Read Until    ${dut} login:    timeout=30s
        Close Console
    END
```

`Bind Driver` points one role library, by its `AS` name, at a driver.
Bindings reset after each test. The `python/examples/robot/multi-dut` example
drives two mock boards this way. If two groups of identical devices have a
driver at the same relative path (such as `power`) with different client
classes, only the first group gets role libraries; the other keeps keywords
named after each driver's path, and no device list.

## How a session gets its lease

As with the Python `JumpstarterTest` helper, a run gets a lease in one of
three ways:

1. **A lease created outside the run**, such as with
   `jmp create lease -l board=rpi4 --duration 4h`. The session attaches to it
   by name, with the `JMP_LEASE` environment variable (which `jmp` and the
   Python client honor too) or the session's lease name variable, or
   automatically: a session with a selector (or exporter name) attaches to
   this client's active lease with exactly the same selector (see
   [Reusing a lease](#reusing-a-lease)). This is what makes runs from the
   editor work after one `jmp create lease`.
2. **`jmp shell`**: inside `jmp shell --selector board=rpi4 -- robot ...`,
   every session uses the shell's lease.
3. **`Acquire Lease`**, for example in the suite setup of an `__init__.robot`
   (see [Managing the lease in the suite](#managing-the-lease-in-the-suite)).
   It is the only thing that acquires a lease.

A session that opens on its own, when a keyword first needs it, uses the
first source that applies:

1. the `jmp shell` the run is inside;
2. a lease name: the session's `lease_name`, else `JMP_LEASE`;
3. the active lease of this client that matches the session's `selector` or
   `exporter_name`;
4. the session's exporter config, served in-process ({term}`local mode`),
   which is not a lease.

Opening on its own never acquires a lease. A session with a selector and no
matching lease fails, rather than falling back to its exporter config, with
a message like this one, which names the three ways:

```text
session 'rpi4': no active lease of client 'ci' matches board=rpi4; create one (`jmp create lease -l board=rpi4 --duration 1h`), run inside `jmp shell`, or call `Acquire Lease`
```

With lease reuse off (`reuse-lease: false`, as for a pabot pool), a session
does not look for a matching lease, so only a named lease or `Acquire Lease`
connects it.

The generated resource holds these sources in variables, so the command line
decides. For session `rpi4`:

```console
$ robot -d results tests/                                        # the resource's own source
$ jmp shell --selector board=rpi4 -- robot -d results tests/     # the shell's lease
$ robot -d results -v JMP_RPI4_SELECTOR:board=rpi4 tests/        # attach to the lease matching board=rpi4
$ robot -d results -v JMP_RPI4_LEASE_NAME:my-lease tests/        # attach to a named lease
$ JMP_LEASE=my-lease robot -d results tests/                     # attach every session to a named lease
$ robot -d results -v JMP_RPI4_CLIENT_CONFIG:lab-eu -v JMP_RPI4_SELECTOR:board=rpi4 tests/
```

`-v JMP_RPI4_EXPORTER_CONFIG:other.yaml` serves another exporter config when
the session names no lease and has no selector. A `default`-session resource
(for example one generated with `-o` and no project file) uses `JMP_SELECTOR`,
`JMP_LEASE_NAME`, `JMP_CLIENT_CONFIG` and `JMP_EXPORTER_CONFIG`. A named
session without its own selector, lease name or client config also accepts
`-v JMP_SELECTOR`, `-v JMP_LEASE_NAME` and `-v JMP_CLIENT_CONFIG`. Only
values of the run's global scope count:
[command-line variables](https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#command-line-variables),
a `-V` variable file or `Set Global Variable`. A `default`-session resource in
the same suite never supplies them.

Inside `jmp shell`, the run always uses the shell's lease and never releases
it. Otherwise it attaches to or acquires a lease with the client config named
by `config` (an alias from `jmp config client list`, a client config YAML
path, or `env`), else `JMP_CLIENT_CONFIG`, else the current client of
`jmp config client use`. The run releases leases `Acquire Lease` acquired and
leaves attached leases with their owner.
A [timeout](https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#timeouts)
that expires while waiting for a lease fails the test and cancels the request.
A [dry run](https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#dry-run)
never leases or connects.

## Reusing a lease

Lease reuse is automatic. A session with a `selector` or `exporter_name`
and no lease name looks for an active lease of the same client, or one shared
with it, with exactly the same selector, and attaches to it: when it opens on
its own, and in `Acquire Lease` before acquiring a new lease.
Selectors must be equivalent
[label selectors](https://kubernetes.io/docs/concepts/overview/working-with-objects/labels/):
the same labels and expressions in any order, with `a!=x` equal to
`a notin (x)`. Narrower or wider selectors never match. With `exporter_name`,
the lease must be for that exporter. Among several matches, the most recently
started lease wins, and the run logs its choice:

```text
Jumpstarter: reusing lease <name> for session 'rpi4'
```

A reused lease is attached, like a `lease_name`, so the run never releases
it. A lease `Acquire Lease` acquired is released when the run ends, so only
leases created outside Robot Framework, such as with `jmp create lease`, carry
over between runs. A `lease_name`, a `JMP_LEASE` environment variable or
`jmp shell` already names the lease, so no search happens.

Within a run, a session never reuses a lease another session holds, so two
sessions with the same selector get separate exporters.
[pabot](https://pabot.org/) processes are separate runs and can attach to the
same externally created lease. Give them different selectors, or turn reuse
off with `reuse-lease: false` in the testbed, `reuse_lease=False` on the
`Jumpstarter` or `Session` import that declares the session, or `reuse=False`
on `Acquire Lease`, and acquire a lease per process with `Acquire Lease` (see
[Parallel runs with pabot](#parallel-runs-with-pabot)).

## When a lease ends

If a lease ends, is transferred or is revoked during the run, the run stops.
With `on_lease_end=fail` on the `Jumpstarter` library, only the keyword
fails. The setting applies to the whole run, and imports without it, such as
the one in every generated resource, leave it unchanged. Set it in the suite
folder's `__init__.robot`:

```robotframework
*** Settings ***
Library    jumpstarter_robot.Jumpstarter    on_lease_end=fail
```

It also works next to `Resource    client.resource` in a suite file, but Robot
Framework then warns that it ignores one of the two `Jumpstarter` imports. A
`Session` import accepts `on_lease_end` for its own session, which takes
precedence over the run's setting.

## Managing the lease in the suite

`Acquire Lease` is the programmatic way to get a lease, and the only one
that acquires. Call it in a suite setup, usually of the suite folder's
`__init__.robot` so that every suite shares the lease, and `Release Lease` to
end it early:

```robotframework
*** Settings ***
Library           jumpstarter_robot.Jumpstarter
Suite Setup       Acquire Lease    selector=board=rpi4    duration=1 hour    config=lab
Suite Teardown    Release Lease

*** Test Cases ***
Exporter Is Leased
    Lease Should Be Active
    ${exporter}=    Get Exporter Name
    &{labels}=    Get Exporter Labels
    Log    ${exporter} ${labels}
```

Arguments not given to `Acquire Lease` come from the session's declaration,
so for a generated resource's session, `Acquire Lease    duration=2 hours`
keeps the resource's selector and client config. It then connects the
session from the first of:

- the surrounding `jmp shell`;
- a lease name (`lease_name=`, the session's own, or `JMP_LEASE`), attached
  to and never released;
- with reuse on (`reuse`, defaulting to the session's `reuse_lease`), an
  active lease of this client with exactly the same selector, attached to and
  never released;
- a new lease for the selector or exporter name, released at `Release Lease`
  or when the run ends.

On a session that declares only an exporter config, `Acquire Lease` serves it
in-process and returns no lease name, so one suite setup works on the bench
and in the lab. On a session that is already connected, for example by an
earlier keyword, it returns that session's lease; given a new selector,
exporter name or lease name, it fails (use `Release Lease` first).

A `Jumpstarter` import that declares a selector and auto-imports driver
keywords (zero configuration, see {doc}`Client resources <client-resources>`)
only warns when there is no lease to attach to at import; `Acquire Lease`
then connects and imports the keywords.

`Start Local Exporter` and `Stop Local Exporter` start and stop an exporter
config explicitly, and `Connect To Shell Session` connects to the surrounding
`jmp shell`.

## In CI

Run the suites inside `jmp shell`, or call `Acquire Lease` in a suite setup,
such as `Suite Setup    Acquire Lease    session=rpi4` in `tests/__init__.robot`.
A CI job has no lease to attach to, so a selector alone is not enough. To
acquire, set `config = "env"` on the client in `robot.toml` or pass the
session's client config variable (`-v JMP_RPI4_CLIENT_CONFIG:env` for client
`rpi4`, whose `selector` is `board=rpi4`), and provide the controller and
credentials as environment variables, with the token from the CI system's
secrets:

````{tab} GitHub
```yaml
# .github/workflows/hardware-test.yml
jobs:
  hardware-test:
    runs-on: ubuntu-latest
    env:
      JMP_ENDPOINT: jumpstarter.example.com:443
      JMP_NAMESPACE: lab
      JMP_NAME: ci
      JMP_TOKEN: ${{ secrets.JMP_TOKEN }}
      JMP_DRIVERS_ALLOW: jumpstarter_driver_*
    steps:
      - uses: actions/checkout@v4
      - run: robot -d results -v JMP_RPI4_CLIENT_CONFIG:env tests/
```
````

````{tab} GitLab
```yaml
# .gitlab-ci.yml
hardware-test:
  variables:
    JMP_ENDPOINT: jumpstarter.example.com:443
    JMP_NAMESPACE: lab
    JMP_NAME: ci
    JMP_DRIVERS_ALLOW: jumpstarter_driver_*
  script:
    - robot -d results -v JMP_RPI4_CLIENT_CONFIG:env tests/
```
````

On GitLab, define `JMP_TOKEN` as a masked CI/CD variable. `JMP_DRIVERS_ALLOW`
lists the driver client classes the run may load, like `drivers.allow` in a
client config. Alternatively, commit a client config YAML with the endpoint
and TLS settings but no token, point `config` at it, and pass the token in
`JMP_TOKEN`. Never commit a token: the generator rejects credential keys in
`robot.toml` and warns when a committed client config contains one. See
{doc}`Loading Order <../../../getting-started/configuration/loading-order>` for
how environment variables combine with config files.

A testbed keeps the CI job short: commit `testbeds/ci.yaml` with the
selectors and `config: env`, and run `robot -d results -V testbeds/ci.yaml
tests/`. Inside `jmp shell` the job needs neither:
`jmp shell --selector board=rpi4 -- robot -d results tests/`.

## Parallel runs with pabot

[pabot](https://github.com/mkorpela/pabot) runs suites, or with
`--testlevelsplit` tests, in parallel processes. Each process is its own Robot
Framework run and holds its own sessions:

- In local mode, each process serves its own exporter config, so the
  `python/examples/robot/lab` suites run in parallel unchanged, from that
  folder:

  ```console
  $ pabot --processes 4 -d results -V testbeds/mock.yaml tests/
  ```

- Against a controller, each process's suite setup (`Acquire Lease`)
  acquires its own board from the boards the selector matches, and releases
  it at the end. Use a pool testbed with `reuse-lease: false`, so that
  processes never attach to the same lease created outside the run, and an
  `acquisition-timeout` long enough to wait for a free board:

  ```yaml
  # python/examples/robot/lab/testbeds/lab-pool.yaml
  JUMPSTARTER:
    config: lab
    selector: board=mockdut,pool=ci
    reuse-lease: false
    acquisition-timeout: 30 minutes
  PASSWORD: change-me
  ```

  ```console
  $ pabot --processes 4 -d results -V testbeds/lab-pool.yaml tests/
  ```

To pin each process to a different board instead, give each its own
`--argumentfileN` with a different `-V` testbed or
`--variable JMP_BOARD_SELECTOR:board=mockdut,slot=N`. pabot's own PabotLib listens
on port 8270, so a remote library server used in the same run needs another
port.
