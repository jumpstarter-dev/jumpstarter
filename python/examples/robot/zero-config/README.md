# Zero-config example

Suites with no client resource. They import `jumpstarter_robot.Jumpstarter`
with the [basic example](../basic/README.md)'s `exporter.yaml`; when the run
connects, the library imports a keyword library for every driver it finds.
The library's own keywords also reach any driver by its path.

## Run it

Install the workspace once (`uv sync --all-packages --all-extras` in
`python/`), then, from the repository root:

```console
$ cd python/examples/robot/zero-config
$ uv run robot -d results tests/
```

The library serves `exporter.yaml` in-process. Inside `jmp shell` it uses the
shell's lease instead, and with `JMP_LEASE=<name>` a lease you created.

## Keywords that exist only at run time

The driver keywords, such as `Dut Power On` or `dut.power.Dut Power Off`, are
imported when the run connects, so the editor cannot know them beforehand. The
`# robotcode: ignore[KeywordNotFound]` comments in `tests/zero_config.robot`
keep [RobotCode](https://robotcode.io/) from flagging them. Generate a client
resource, as the basic example does, to get the same keywords in the editor
without those comments.

## Files

| Path | |
| --- | --- |
| `robot.toml` | RobotCode settings; no client to generate |
| `tests/zero_config.robot` | `List Drivers`, the auto-imported keywords by name and by qualified name, and `List Driver Keywords` |
| `tests/by_path.robot` | Drivers by path: `Call Driver` for any client method, `Open Driver Context` and `Close Driver Context`, `Collect Driver Stream` and `Get Driver Property` |
