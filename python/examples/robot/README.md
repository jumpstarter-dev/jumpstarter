# Robot Framework Examples

[Robot Framework](https://robotframework.org/) suites that drive Jumpstarter
drivers with [`jumpstarter-robot`](../../packages/jumpstarter-robot). Most run
in local mode: mock boards served in-process, with no hardware or controller.
The [Robot Framework guide](../../../docs/source/getting-started/guides/robot-framework.md)
walks through them.

## Leases

To run an example on a lab exporter instead, the run gets a lease in one of
three ways, as with the Python helper `JumpstarterTest`:

1. **A lease you created**, which the run attaches to and never releases.
   Create it with `jmp create lease -l board=mock --duration 2h`, then give
   the session the same selector (`-v JMP_SELECTOR:board=mock`) or the lease
   name (`JMP_LEASE=<name>`). Runs from the editor attach the same way once a
   testbed or RobotCode profile gives the selector.
2. **`jmp shell`**: `jmp shell --selector board=mock -- robot -d results tests/`
   uses the shell's lease for every session.
3. **`Acquire Lease`** in the suites, as the [`lab`](lab/README.md) example's
   `tests/__init__.robot` does. It is the only thing that acquires a lease, and
   the run releases it at the end.

A session opening on its own only attaches: with a selector and no matching
lease it fails, naming these three ways. See
[Lease management](../../../docs/source/getting-started/guides/robot-framework.md#lease-management)
in the guide.

## Install

Install the workspace once, from `python/`:

```console
$ uv sync --all-packages --all-extras
```

## Examples

Each folder is an example with its own `README.md`,
`robot.toml`, `tests/` and `.vscode/` settings; open it as its own workspace
folder in VS Code.

| Folder                                        | Shows                                                                                                                                                                     |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`basic/`](basic/README.md)                   | **Start here.** One mock board: power, serial console, storage, shell, and keywords curated by the board's client                                                         |
| [`multi-dut/`](multi-dut/README.md)           | Two identical boards driven by one set of keywords: role libraries, `Use Device` and `Bind Driver`                                                                        |
| [`lab/`](lab/README.md)                       | Testbeds for a mock, a lab and a pool of boards, and parallel runs with [pabot](https://github.com/mkorpela/pabot)                                                        |
| [`zero-config/`](zero-config/README.md)       | No client resource: keywords imported at run time, and drivers reached by path                                                                                            |
| [`remote-library/`](remote-library/README.md) | A Robot Framework remote library on the board, reached through a Jumpstarter port forward                                                                                 |
| [`linux-ssh/`](linux-ssh/README.md)           | A Linux machine over SSH with [SSHLibrary](https://github.com/MarketSquare/SSHLibrary)                                                                                    |
| [`mobile/`](mobile/README.md)                 | Android emulator and iOS simulator apps with [Appium](https://appium.io/docs/en/latest/) and [AppiumLibrary](https://github.com/serhatbolsu/robotframework-appiumlibrary) |

## Shared code

| Path                                   |                                                                                                                                                                                                                    |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `pyproject.toml`                       | The workspace member `jumpstarter-example-robot`: installs `jumpstarter_example_robot` and every example's dependencies (drivers, SSHLibrary, AppiumLibrary, pabot), with pytest and RobotCode as dev dependencies |
| `jumpstarter_example_robot/mockdut.py` | The mock board's drivers: `MockDut`, with the children `MockDutPower` and `MockDutConsole`, a console that prints a boot log and a login prompt on power on. Used by `basic`, `multi-dut`, `lab` and `zero-config` |
| `jumpstarter_example_robot/client.py`  | `MockDutClient`, the board's client, which curates its keywords: `@robot.keyword` makes `Reboot` and `Should Be Powered`, `@robot.not_keyword` hides a Python-only helper                                          |

`lab` generates its client resource from `basic/exporter.yaml` and flashes
`basic/data/image.bin`; the `zero-config` suites serve `basic/exporter.yaml`
directly.

## Tests

`jumpstarter_example_robot/examples_test.py` runs the examples and checks that:

- the `basic`, `multi-dut` and `zero-config` suites pass in local mode;
- the `lab` suites pass on the mock board, with and without
  `testbeds/mock.yaml`, and in parallel with pabot, and on a lab testbed their
  suite setup's `Acquire Lease` is what leases the board;
- the `remote-library` suite imports its library through a port forward;
- the `mobile` suites treat their device as remote only when a session
  variable names a selector or a lease (no device needed);
- RobotCode's REPL drives the basic board, and `Acquire Lease` there imports
  the board's keywords;
- RobotCode's analysis finds every keyword in every example;
- every committed client resource matches what `jmp robot generate` writes
  now.

`jumpstarter_example_robot/mockdut_test.py` checks the mock board itself:
boot log, login and commands on the console, the client's curated methods and
power readings.

Run them from `python/`:

```console
$ uv run pytest examples/robot
```

A client resource is stale when the exporter config or `robot.toml` it is
generated from changed without regenerating it. Run `uv run jmp robot generate`
in that example's folder and commit the regenerated resource.
