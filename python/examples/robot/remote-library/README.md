# Remote library example

A Robot Framework
[remote library](https://github.com/robotframework/RemoteInterface) server
that runs on the board, reached through a Jumpstarter port forward.
`Open Port Forward` forwards a local port through the exporter's `TcpNetwork`
driver to the server, and Robot Framework's `Remote` library imports the
board's keywords from there. The board needs no route to the test runner.

## Run it

Install the workspace once (`uv sync --all-packages --all-extras` in
`python/`), then, from the repository root:

```console
$ cd python/examples/robot/remote-library
$ uv run robot -d results tests/
```

The suite setup starts `board_remote_server.py` on `127.0.0.1:8271` to play
the board, serves `exporter.yaml` in-process, forwards a port to the `remote`
driver and imports the server as the library `Board`. The suite teardown
stops the server.

The server uses port 8271 rather than 8270, the remote library default,
because [pabot](https://github.com/mkorpela/pabot)'s PabotLib listens on 8270.

## On a real board

Run a remote library server on the board, usually with
[PythonRemoteServer](https://github.com/robotframework/PythonRemoteServer)
(`pip install robotremoteserver`), and point the exporter's `TcpNetwork`
driver at the board's address and the server's port. Drop the suite setup
steps that start and wait for `board_remote_server.py`; the rest stays the
same.

The suite does not acquire a lease: run it inside `jmp shell` on that
exporter, or with `JMP_LEASE=<name>` naming a lease you created.

## Files

| Path | |
| --- | --- |
| `exporter.yaml` | A `TcpNetwork` driver `remote`, pointed at `127.0.0.1:8271` |
| `board_remote_server.py` | A remote library server standing in for the board, standard library only: `Get Temperature`, `Set Led`, `Led Should Be` |
| `robot.toml` | RobotCode settings |
| `tests/remote_library.robot` | Connect through the port forward, run the board's keywords, and see a board failure fail the test |
