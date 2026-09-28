# Windows

The Jumpstarter client, {term}`exporter` and `jmp` CLI run natively on 64-bit
Windows with Python 3.12 or newer. Continuous integration runs every Windows
package suite on Windows Server 2025 with Python 3.12, 3.13 and 3.14.

## What works

- **CLI**: `jmp` and `j`, including `jmp config`, `jmp login`, `jmp get`,
  `jmp create`/`jmp delete` and `jmp admin`. `jmp admin` uses the same external
  tools as on Linux (for example `kubectl`, `helm`, `kind` or `minikube`), which
  must be on `PATH`.
- **`jmp shell`**: starts PowerShell 7 (`pwsh`) when installed, then Windows
  PowerShell, then `cmd.exe`. Set `SHELL` to choose another shell such as Git
  Bash. The prompt shows the exporter, `shell.use_profiles` controls profile
  loading, and `NO_COLOR`/`NO_ICONS` apply. Ctrl+C interrupts the command
  running inside the shell without ending the session. `jmp shell -- <command>`
  runs a single command and returns its exit code.
- **`jmp run`**: runs the exporter in a supervised worker process. Ctrl+C or
  Ctrl+Break stops it gracefully (pressing again stops it immediately), worker
  restarts follow the same policy as on Linux, and processes started by drivers
  are stopped with the exporter, even if the supervisor is killed.
- **Hooks**: exporter lifecycle hooks run PowerShell by default, and also Python,
  `cmd.exe` and Git Bash scripts. See [Hooks on Windows exporters](../../introduction/hooks.md#hooks-on-windows-exporters).
- **Local sessions** use Unix domain sockets in a directory only the current user
  and SYSTEM can access, as on Linux.

## Drivers

Driver packages gain Windows support individually. The test suites of these
drivers run on Windows in CI; the notes say what those tests exercise:

| Driver | Notes |
| --- | --- |
| composite | |
| {doc}`power <../../reference/package-apis/drivers/power>` | `MockPower`; physical power backends are tested separately |

Other drivers are not yet supported on Windows. Clients connect to exporters of
any platform, so a Windows client can use a Linux exporter's drivers once their
client packages support Windows. A driver client that cannot load on Windows is
replaced by a placeholder with a warning, and the rest of the exporter's drivers
remain usable.

Drivers that need Linux kernel interfaces or Linux-only tools on the exporter
host (such as `dut-network`, `dutlink`, `gpiod`, `iscsi`, `nanokvm-usb`, `qemu`,
`renode`, `sdwire`, `stlink-msd` and `ustreamer`) require a Linux exporter.

## Installation

On Windows, the `jumpstarter` package depends on `jumpstarter-core`, a native
extension that provides local sockets and process containment. Until prebuilt
Windows wheels are published, install from a source checkout. This needs
[uv](https://docs.astral.sh/uv/), Rust and the MSVC C++ build tools with a
Windows SDK:

```powershell
git clone https://github.com/jumpstarter-dev/jumpstarter.git
cd jumpstarter\python
uv sync --locked --package jumpstarter-cli --package jumpstarter-driver-power
uv run --no-sync jmp --help
```

Add `--package` for each additional driver package you need. The `install.sh`
installer and `jmp self update` are not available on Windows; update the
checkout and run `uv sync` again instead. Shell completion is available for
Bash, Zsh and Fish only.

## Configuration files

Configuration uses the same layout as Linux under
`%USERPROFILE%\.config\jumpstarter` (or `XDG_CONFIG_HOME`/`JMP_CLIENT_CONFIG_HOME`
when set). System-wide exporter configurations are read from
`%PROGRAMDATA%\jumpstarter\exporters`. See [Files](../configuration/files.md).

Client credentials are protected by the user profile's permissions; POSIX file
modes have no effect on Windows.

## Limitations

- When a lease ends, `jmp shell` stops the shell process. Programs the shell
  started in the background are not stopped.
- The `jumpstarter-exec` sidecar used by container-based provisioners requires
  Linux.
- Continuous integration uses Windows Server; desktop Windows releases are
  validated with the manual [Windows end-to-end runners](https://github.com/jumpstarter-dev/jumpstarter/blob/main/e2e/windows/README.md).
