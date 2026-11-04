# Lab example

The same suites on a mock board, on a board leased from your lab, or in
parallel on a pool of boards. A *testbed*, a Robot Framework
[YAML variable file](https://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#variable-file-as-yaml),
says where the session connects; the suites do not change.

The board is the [basic example](../basic/README.md)'s mock board: `client.resource`
is generated from `../basic/exporter.yaml`, for the session `board`.

## Run it

Install the workspace once (`uv sync --all-packages --all-extras` in
`python/`), then, from the repository root, pick a testbed:

```console
$ cd python/examples/robot/lab
$ uv run robot -d results -V testbeds/mock.yaml tests/   # local mode
$ uv run robot -d results -V testbeds/lab.yaml tests/    # a board from your lab
```

Without `-V` the suites run in local mode, as with `mock.yaml`.

## How the suites get their board

`tests/__init__.robot` connects the session `board` once, for all suites, in
its suite setup:

```robotframework
Suite Setup         Acquire Lease    session=board
```

`Acquire Lease` is the only thing that acquires a lease. What it does depends
on the testbed:

- **`mock.yaml`, or no testbed**: the session declares only an exporter
  config, so `Acquire Lease` serves the mock board in-process and returns no
  lease name. The same suite setup works on the bench and in the lab.
- **`lab.yaml`**: `Acquire Lease` attaches to an active lease of your client
  for exactly the same selector, if you created one; otherwise it acquires a
  new lease for 1 hour and releases it when the run ends.
- **`lab-pool.yaml`**: lease reuse is off, so `Acquire Lease` always acquires
  a lease of its own, waiting up to `acquisition-timeout` for a free board.

Inside `jmp shell`, `Acquire Lease` uses the shell's lease, whatever the
testbed. With `-v JMP_BOARD_LEASE_NAME:<name>` or `JMP_LEASE=<name>` it
attaches to that lease. A lease the run attached to is never released.

On a lab testbed, a single file run on its own, such as `tests/power.robot`,
skips `tests/__init__.robot`, so nothing acquires a lease: the session only
attaches, to a lease you created for the testbed's selector or to one you
named (with `lab-pool.yaml`, only to one you named). To run one suite with the
suite setup, select it from the folder:
`uv run robot -d results -V testbeds/lab.yaml --suite power tests/`.

## Testbeds

| Testbed | Connects to |
| --- | --- |
| `testbeds/mock.yaml` | The mock board, served in-process. It adds only test data (`PASSWORD`) |
| `testbeds/lab.yaml` | A board from a controller, by selector: the lease you created for that selector, else a new lease for 1 hour |
| `testbeds/lab-pool.yaml` | One board per process from a pool, for [pabot](https://github.com/mkorpela/pabot): `reuse-lease: false`, so every process acquires its own lease, and an `acquisition-timeout` to wait for a free board |

`lab.yaml` and `lab-pool.yaml` are templates. To point them at your lab:

- set `config` to your client config alias (`jmp config client list`), or to
  a client config path;
- set `selector` to the labels of exporters that have the same drivers as
  `../basic/exporter.yaml` (`dut` with `power` and `console`, `storage`, `shell`);
- pass the password with `-v PASSWORD:...` rather than committing it.

A session variable on the command line still wins over the testbed, for
example `-v JMP_BOARD_SELECTOR:board=x` or `-v JMP_BOARD_LEASE_NAME:my-lease`.

To run the suites repeatedly, or from the editor, create the lease once and
keep it. Every run with `lab.yaml` then attaches to it, and none releases it:

```console
$ uv run jmp create lease --client lab -l board=mockdut,lab=eu --duration 2h
$ uv run robot -d results -V testbeds/lab.yaml tests/
```

Delete it with `jmp delete lease --client lab <name>` when you are done.

## RobotCode profiles

`robot.toml` lists the testbeds as the profiles `mock`, `lab` and `lab-pool`.
Open this folder as its own workspace folder in VS Code and pick a profile in
[RobotCode](https://robotcode.io/)'s profile selector, or from a terminal:

```console
$ uv run robotcode --profile lab robot
```

## Parallel runs with pabot

pabot runs the suites in parallel processes, and each process runs the suite
setup of `tests/__init__.robot`. In local mode each process serves its own
mock board; with `lab-pool.yaml` each acquires its own board from the pool and
releases it at the end:

```console
$ uv run pabot --processes 4 -d results -V testbeds/mock.yaml tests/
$ uv run pabot --processes 4 -d results -V testbeds/lab-pool.yaml tests/
```

Use `lab-pool.yaml` rather than `lab.yaml` for pabot: with a matching lease
active, every process would attach to that one board.

## Files

| Path | |
| --- | --- |
| `robot.toml` | RobotCode settings, the profiles `mock`, `lab` and `lab-pool`, and the client `board` |
| `client.resource` | **Generated** client resource, session `board`; regenerate with `uv run jmp robot generate` |
| `testbeds/` | `mock.yaml`, `lab.yaml` and `lab-pool.yaml` |
| `tests/__init__.robot` | The suite setup `Acquire Lease    session=board`, shared by every suite |
| `tests/boot.robot` | Power on and log in on the console with the testbed's `PASSWORD` |
| `tests/flash.robot` | Flash `../basic/data/image.bin` and boot from it |
| `tests/power.robot` | Power status and power readings |
