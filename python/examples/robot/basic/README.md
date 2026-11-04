# Basic example

The mock board from the
[Robot Framework guide](../../../../docs/source/getting-started/guides/robot-framework.md):
[Robot Framework](https://robotframework.org/) suites that drive Jumpstarter
drivers with [`jumpstarter-robot`](../../../packages/jumpstarter-robot). The
board is served in this process ("local mode"), so the suites need no
hardware, controller or `jmp shell`.

## Run it

Install the workspace once, from `python/`:

```console
$ uv sync --all-packages --all-extras
```

Then, from the repository root, run the suites:

```console
$ cd python/examples/robot/basic
$ uv run robot -d results tests/
```

The first keyword that needs a driver serves `exporter.yaml`, and the run
stops it at the end. Any single file works the same:
`uv run robot -d results tests/storage.robot`. Results are in
`results/log.html`.

### Run against a real board

The same suites run unchanged on a lab exporter that serves the drivers of
`exporter.yaml`. These suites never acquire a lease themselves, so either
attach them to a lease you created or run them inside `jmp shell`.

Create the lease, then attach by its selector or by its name. The run does not
release it, so it serves run after run until you delete it with
`jmp delete lease <name>`:

```console
$ uv run jmp create lease -l board=mock --duration 2h
$ uv run robot -d results -v JMP_SELECTOR:board=mock tests/   # the active lease for this selector
$ JMP_LEASE=<name> uv run robot -d results tests/             # the lease by name
```

Inside `jmp shell` the suites use the shell's lease:

```console
$ uv run jmp shell --selector board=mock -- robot -d results tests/
```

`jmp shell --exporter-config exporter.yaml` does the same with the mock board
served locally.

With a selector and no matching lease, the first keyword that needs the board
fails with a message naming the ways to get one. To acquire the lease in the
suites instead, with `Acquire Lease`, see the [lab example](../lab/README.md).

### In VS Code with RobotCode

Open `python/examples/robot/basic` as a workspace folder, install the
recommended [RobotCode](https://robotcode.io/) extension, and select the
`python/.venv` interpreter (`.vscode/settings.json` points at it). RobotCode
reads `robot.toml`, so the run and debug buttons and the Test Explorer work on
any test, file or folder, and keywords complete from `client.resource`.

To run them from the editor on a lease you created, give the session its
selector in `robot.toml`; every run from the editor then attaches to that
lease:

```toml
[variables]
JMP_SELECTOR = "board=mock"
```

Remove it to run in local mode again.

## Regenerate the client resource

`client.resource` is generated from `exporter.yaml` by the settings in
`robot.toml` (`[tool.jumpstarter.client.default]`, the `default` session).
Each driver's keywords carry its path in the tree: `dut.power` gives
`Dut Power On`, `dut` gives `Dut Reboot`, `storage` gives `Storage Flash`. An
alias in `robot.toml` chooses another prefix. After changing either file, run
from this folder:

```console
$ uv run jmp robot generate
```

and commit `client.resource`.

## Files

| Path | |
| --- | --- |
| `exporter.yaml` | The mock board: `dut` with `power` and a serial `console`, a storage mux `storage`, and a `shell` driver with the methods `hello` and `fail` |
| `robot.toml` | RobotCode settings and the client generation settings |
| `client.resource` | **Generated** client resource: one keyword library per driver |
| `data/image.bin` | The image that `tests/storage.robot` flashes |
| `tests/power_and_console.robot` | Power on, log in on the console, run a command, check the power status |
| `tests/storage.robot` | Flash `data/image.bin` through the storage mux; `Call Driver` for any client method |
| `tests/shell.robot` | The shell methods `Shell Hello` and `Shell Fail`, and their return codes |
| `tests/custom_keywords.robot` | `Dut Reboot` and `Dut Should Be Powered`, keywords curated by the board's client |
| `.vscode/` | The recommended extensions and the `python/.venv` interpreter |

The mock board's drivers and client are shared with the other examples, in
`../jumpstarter_example_robot/`; see [Shared code](../README.md#shared-code).
