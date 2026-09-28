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
  runs a single command and returns its exit code. When the lease ends, the
  shell and every process it started are stopped; after a normal exit,
  background processes keep running, as with POSIX shells.
- **`jmp run`**: runs the exporter in a supervised worker process. Ctrl+C or
  Ctrl+Break stops it gracefully (pressing again stops it immediately), worker
  restarts follow the same policy as on Linux, and processes started by drivers
  are stopped with the exporter, even if the supervisor is killed.
- **Hooks**: exporter lifecycle hooks run PowerShell by default, and also Python,
  `cmd.exe` and Git Bash scripts. See [Hooks on Windows exporters](../../introduction/hooks.md#hooks-on-windows-exporters).
- **Serial console**: `j serial console` works in Windows Terminal and the
  console host, with UTF-8 text and ANSI output. Press Ctrl+B three times to
  exit; Ctrl+C is sent to the target.
- **Local sessions** use Unix domain sockets in a directory only the current user
  and SYSTEM can access, as on Linux.

## Drivers

Driver packages gain Windows support individually. The test suites of these
drivers run on Windows in CI; the notes say what those tests exercise:

| Driver | Notes |
| --- | --- |
| {doc}`adb <../../reference/package-apis/drivers/adb>` | Requires the Android SDK platform tools (`adb.exe`); tests use a mocked `adb` |
| {doc}`ble <../../reference/package-apis/drivers/ble>` | Client streams and pexpect; the interactive BLE console is not available on Windows |
| composite | |
| {doc}`energenie <../../reference/package-apis/drivers/energenie>` | Tested with a mocked EnerGenie power socket |
| {doc}`flashers <../../reference/package-apis/drivers/flashers>` | Tested with mocked targets and the committed test OCI bundle |
| {doc}`network <../../reference/package-apis/drivers/network>` | TCP, UDP and WebSocket drivers, port forwarding and pexpect; Unix-socket and D-Bus drivers need a POSIX exporter |
| {doc}`opendal <../../reference/package-apis/drivers/opendal>` | Storage and file transfer operations; `MockStorageMux` reopens its backing file on Windows |
| {doc}`pi-pico <../../reference/package-apis/drivers/pi-pico>` | UF2 flashing to a mounted BOOTSEL drive; tested with a simulated drive |
| {doc}`power <../../reference/package-apis/drivers/power>` | `MockPower`; physical power backends are tested separately |
| {doc}`probe-rs <../../reference/package-apis/drivers/probe-rs>` | Requires the `probe-rs` executable; tests use a mocked tool |
| {doc}`pyserial <../../reference/package-apis/drivers/pyserial>` | Serial streams, `j serial pipe`, pexpect and the interactive `j serial console`; tested with `loop://` rather than COM hardware |
| {doc}`shell <../../reference/package-apis/drivers/shell>` | Methods run through the configured `shell`; the default `bash` needs a native Bash such as Git Bash |
| {doc}`ssh <../../reference/package-apis/drivers/ssh>` | Uses the Windows OpenSSH client (`ssh.exe`); temporary identity files are private to the current user |
| {doc}`tmt <../../reference/package-apis/drivers/tmt>` | Requires `tmt` on the client; tests use a mocked tool |

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
extension that provides local sockets and process containment. Starting with
Jumpstarter 0.10.0, each release publishes `jumpstarter-core` wheels for Windows
x64 and ARM64 to PyPI and to the GitHub release, so no compiler is needed.
Install the CLI and the drivers you need from PyPI, for example with
[uv](https://docs.astral.sh/uv/):

```powershell
uv tool install jumpstarter-cli --with jumpstarter-driver-power
jmp --help
```

The pkg.jumpstarter.dev index does not carry the Windows native wheel; use PyPI
on Windows.

To run an unreleased version, install from a source checkout. This needs uv,
Rust and the MSVC C++ build tools with a Windows SDK:

```powershell
git clone https://github.com/jumpstarter-dev/jumpstarter.git
cd jumpstarter\python
uv sync --locked --package jumpstarter-cli --package jumpstarter-driver-power
uv run --no-sync jmp --help
```

Add `--package` for each additional driver package you need. The `install.sh`
installer and `jmp self update` are not available on Windows; upgrade with
`uv tool upgrade jumpstarter-cli`, or update the checkout and run `uv sync`
again. Shell completion is available for
Bash, Zsh and Fish only.

## Configuration files

Configuration uses the same layout as Linux under
`%USERPROFILE%\.config\jumpstarter` (or `XDG_CONFIG_HOME`/`JMP_CLIENT_CONFIG_HOME`
when set). System-wide exporter configurations are read from
`%PROGRAMDATA%\jumpstarter\exporters`. See [Files](../configuration/files.md).

Client credentials are protected by the user profile's permissions; POSIX file
modes have no effect on Windows.

## Limitations

- The `jumpstarter-exec` sidecar used by container-based provisioners requires
  Linux.
- Continuous integration uses Windows Server; desktop Windows releases are
  validated with the manual [Windows end-to-end runners](https://github.com/jumpstarter-dev/jumpstarter/blob/main/e2e/windows/README.md).
