# Multi-DUT example

Two identical mock boards, `dut1` and `dut2`, on one exporter, driven by the
same keywords. The client resource is generated with `roles = true`, so the
boards' identical drivers become *role* libraries that `Use Device` points at
one board at a time.

## Run it

Install the workspace once (`uv sync --all-packages --all-extras` in
`python/`, see the [examples index](../README.md)), then, from the repository
root:

```console
$ cd python/examples/robot/multi-dut
$ uv run robot -d results tests/
```

The suite serves `exporter.yaml` in-process. Inside `jmp shell` it uses the
shell's exporter instead:

```console
$ uv run jmp shell --exporter-config exporter.yaml -- robot -d results tests/
```

On a lab exporter with the same two boards, the suite attaches to a lease you
created, by selector or by name; it never acquires one itself:

```console
$ uv run jmp create lease -l board=multi --duration 2h
$ uv run robot -d results -v JMP_MULTI_SELECTOR:board=multi tests/
$ JMP_LEASE=<name> uv run robot -d results tests/
```

## How it works

- The role libraries `multi.power` and `multi.console` give both boards' power
  and console drivers one set of keywords, such as `Power On`. They start
  bound to the first board, `dut1`.
- `Use Device    dut2` points every role library at `dut2`. The binding resets
  after each test.
- `Bind Driver    multi.power    dut2.power` rebinds a single role library, by
  its `AS` name.
- `@{MULTI_DEVICES}` lists the boards (`dut1`, `dut2`); it is named after the
  session, `multi`, so a `FOR` loop covers every board.
- Each board's own driver keeps its own keywords: `Dut1 Reboot`,
  `Dut2 Should Be Powered`.

After changing `exporter.yaml` or `robot.toml`, regenerate the client
resource from this folder with `uv run jmp robot generate`.

## Files

| Path | |
| --- | --- |
| `exporter.yaml` | Two copies of the basic example's mock board, `dut1` and `dut2`, each with `power` and `console` |
| `robot.toml` | RobotCode settings and the client `multi` with `roles = true` |
| `client.resource` | **Generated** client resource: the role libraries, a library per board, and `@{MULTI_DEVICES}` |
| `tests/multi_dut.robot` | Boot every board in a loop, switch boards independently, `Use Device` and `Bind Driver` |
