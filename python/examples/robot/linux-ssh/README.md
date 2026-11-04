# Linux over SSH example

A real Linux machine over SSH. The suite reaches it through a Jumpstarter
`SSHWrapper` driver and then uses
[SSHLibrary](https://github.com/MarketSquare/SSHLibrary) keywords as usual:
`Execute Command`, `Put File`, `Get File`.

## Run it

Install the workspace once (`uv sync --all-packages --all-extras` in
`python/`). The suite targets `127.0.0.1:22` by default and needs a
credential the machine accepts. From the repository root:

```console
$ cd python/examples/robot/linux-ssh
$ SSH_IDENTITY_FILE=~/.ssh/id_ed25519 uv run robot -d results tests/
$ uv run robot -d results -v SSH_HOST:192.0.2.10 -v SSH_USER:root -v SSH_PASSWORD:secret tests/
```

| Variable | Default | |
| --- | --- | --- |
| `SSH_HOST`, `SSH_PORT` | `127.0.0.1`, `22` | Where sshd listens |
| `SSH_USER` | `$USER`, or `root` | Login name |
| `SSH_IDENTITY_FILE` | | Private key, handed to the client by the exporter |
| `SSH_PASSWORD` | | Password login instead of a key |

Each comes from the environment variable of the same name or from `robot -v`.
The suite skips every test, saying why, when nothing listens on
`SSH_HOST:SSH_PORT` or neither `SSH_IDENTITY_FILE` nor `SSH_PASSWORD` is set.
The file copy test needs SFTP on the machine; `--exclude sftp` leaves it out.

## How it works

`exporter.yaml` is a template: the suite setup fills in its `SSH_*`
placeholders, writes the result to `results/exporter-ssh.yaml` and serves it
in-process. `Open SSH Connection` then forwards a local port to the `ssh`
driver, hands SSHLibrary the exporter's key and logs in. `Get SSH Endpoint`
gives the host, port, user and key file for other tools, such as `scp`.

A lab exporter would carry the same two drivers with fixed values.

## Files

| Path | |
| --- | --- |
| `exporter.yaml` | Template: an `SSHWrapper` driver `ssh` with a `TcpNetwork` child `tcp` |
| `robot.toml` | RobotCode settings |
| `tests/linux_ssh.robot` | Run commands, copy a file there and back (tag `sftp`), and get the SSH endpoint |
