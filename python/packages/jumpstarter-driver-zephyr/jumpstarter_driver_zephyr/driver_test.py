import functools
import os
import re
import subprocess
import tarfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import anyio
import pytest
from click.testing import CliRunner
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_pyserial.driver import PySerial

from .client import RESERVED_TWISTER_OPTIONS, ZephyrClient, _render
from .driver import Zephyr
from jumpstarter.client.flasher import FlashPhase, FlashStatus
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _make_build_dir(tmp_path: Path, *, hex_=True, bin_=False, elf=False) -> Path:
    """Create a minimal Zephyr build dir with the requested fixed-name artifacts
    under its ``zephyr/`` subdir."""
    build = tmp_path / "build"
    zdir = build / "zephyr"
    zdir.mkdir(parents=True)
    if hex_:
        (zdir / "zephyr.hex").write_text(":00000001FF\n")
    if bin_:
        (zdir / "zephyr.bin").write_bytes(b"\x00\x01\x02\x03")
    if elf:
        (zdir / "zephyr.elf").write_bytes(b"\x7fELF")
    return build


def _raises_flash_failure(match: str):
    """The driver reports the failure as an ERROR update, which the client raises.

    flash_build_dir() raises it as is; flash() and flash_stream() raise it
    wrapped in the upload's task group.
    """
    return pytest.RaisesGroup(pytest.RaisesExc(RuntimeError, match=match), allow_unwrapped=True, flatten_subgroups=True)


def _statuses(client, build_dir, **kw) -> list[FlashStatus]:
    """Drain the streaming flash and return the updates."""
    return list(cast(ZephyrClient, client).flash_build_dir(str(build_dir), **kw))


def _flash(client, build_dir, **kw) -> list[str]:
    """Drain the streaming flash and return the updates as printed lines."""
    return [_render(status) for status in _statuses(client, build_dir, **kw)]


def test_flash_happy_path(tmp_path):
    """Client tars the artifacts, driver extracts them and runs the command."""
    build = _make_build_dir(tmp_path)
    with serve(Zephyr(flash_command="echo FAKE-FLASH", flash_timeout=0)) as client:
        lines = _flash(client, build)
    out = "\n".join(lines)
    assert "FAKE-FLASH" in out
    assert "Flash complete" in out


def test_flash_reports_phases(tmp_path):
    """The archive is received, then the command's output arrives line by line
    in ``stdout``, then the flash completes."""
    build = _make_build_dir(tmp_path)
    with serve(Zephyr(flash_command="echo one; echo two", flash_timeout=0)) as client:
        statuses = _statuses(client, build)
    download = [s for s in statuses if s.phase == FlashPhase.DOWNLOAD]
    assert download, statuses
    assert download[-1].bytes_transferred
    assert [s.stdout for s in statuses if s.stdout is not None] == ["one", "two"]
    assert statuses[-1].phase == FlashPhase.COMPLETE


def test_flash_returns_final_status(tmp_path):
    """flash() drains the stream and returns the COMPLETE update."""
    build = _make_build_dir(tmp_path)
    archive = tmp_path / "firmware.tar"
    with tarfile.open(archive, "w") as tar:
        tar.add(build / "zephyr" / "zephyr.hex", arcname="zephyr.hex")
    with serve(Zephyr(flash_command="true", flash_timeout=0)) as client:
        status = cast(ZephyrClient, client).flash(str(archive))
    assert status.phase == FlashPhase.COMPLETE


def test_archive_without_artifacts_fails(tmp_path):
    """An upload that holds none of the fixed-name artifacts is an error."""
    other = tmp_path / "other.txt"
    other.write_text("x")
    archive = tmp_path / "firmware.tar"
    with tarfile.open(archive, "w") as tar:
        tar.add(other, arcname="other.txt")
    with (
        serve(Zephyr(flash_command="true", flash_timeout=0)) as client,
        _raises_flash_failure("No firmware artifacts"),
    ):
        cast(ZephyrClient, client).flash(str(archive))


def test_command_runs_in_upload_dir_with_artifact(tmp_path):
    """The command runs with cwd set to the upload dir, where zephyr.hex
    actually exists at flash time."""
    build = _make_build_dir(tmp_path)
    rec = tmp_path / "rec"
    rec.mkdir()
    cmd = f"test -f zephyr.hex && echo HEX_PRESENT; pwd > {rec}/cwd.txt"
    with serve(Zephyr(flash_command=cmd, flash_timeout=0)) as client:
        lines = _flash(client, build)
    assert "HEX_PRESENT" in "\n".join(lines)
    cwd = Path((rec / "cwd.txt").read_text().strip())
    # cwd is the exporter's temporary extraction dir, not the client's build dir.
    assert cwd.name.startswith("zephyr-flash-")
    assert cwd != build / "zephyr"


def test_token_substitution(tmp_path):
    """{hex}/{bin}/{dir} are substituted with absolute paths to existing files."""
    build = _make_build_dir(tmp_path, hex_=True, bin_=True)
    rec = tmp_path / "rec"
    rec.mkdir()
    cmd = (
        f"test -f {{hex}} && echo HEX_OK; "
        f"test -f {{bin}} && echo BIN_OK; "
        f'printf "%s\\n%s\\n" "{{hex}}" "{{dir}}" > {rec}/tokens.txt'
    )
    with serve(Zephyr(flash_command=cmd, flash_timeout=0)) as client:
        lines = _flash(client, build)
    out = "\n".join(lines)
    assert "HEX_OK" in out and "BIN_OK" in out
    hex_path, dir_path = (rec / "tokens.txt").read_text().splitlines()
    assert "{hex}" not in hex_path
    assert hex_path == f"{dir_path}/zephyr.hex"


def test_uploads_all_present_artifacts(tmp_path):
    """hex + bin + elf are all uploaded and extracted on the exporter."""
    build = _make_build_dir(tmp_path, hex_=True, bin_=True, elf=True)
    rec = tmp_path / "rec"
    rec.mkdir()
    with serve(Zephyr(flash_command=f"ls > {rec}/files.txt", flash_timeout=0)) as client:
        _flash(client, build)
    files = set((rec / "files.txt").read_text().split())
    assert {"zephyr.hex", "zephyr.bin", "zephyr.elf"} <= files


@pytest.mark.parametrize("absolute", [False, True])
def test_symlinked_artifact_uploads_its_target(tmp_path, absolute):
    """An artifact that is a symlink is uploaded as the file it points to."""
    build = _make_build_dir(tmp_path, hex_=False)
    zdir = build / "zephyr"
    (zdir / "firmware.hex").write_text(":00000001FF\n")
    (zdir / "zephyr.hex").symlink_to(zdir / "firmware.hex" if absolute else "firmware.hex")
    rec = tmp_path / "rec"
    rec.mkdir()
    cmd = f"test -L zephyr.hex && touch {rec}/is_link; cat zephyr.hex > {rec}/hex.txt"
    with serve(Zephyr(flash_command=cmd, flash_timeout=0)) as client:
        _flash(client, build)
    assert not (rec / "is_link").exists()
    assert (rec / "hex.txt").read_text() == ":00000001FF\n"


def test_only_present_artifacts_uploaded(tmp_path):
    """bin/elf absent locally => not present on the exporter."""
    build = _make_build_dir(tmp_path, hex_=True, bin_=False, elf=False)
    rec = tmp_path / "rec"
    rec.mkdir()
    with serve(Zephyr(flash_command=f"ls > {rec}/files.txt", flash_timeout=0)) as client:
        _flash(client, build)
    files = set((rec / "files.txt").read_text().split())
    assert "zephyr.hex" in files
    assert "zephyr.bin" not in files
    assert "zephyr.elf" not in files


def test_board_id_is_informational(tmp_path):
    """board_id is accepted (twister contract) and surfaced as a log line."""
    build = _make_build_dir(tmp_path)
    with serve(Zephyr(flash_command="true", flash_timeout=0)) as client:
        lines = _flash(client, build, board_id="XDS110-ABC123")
    assert any("XDS110-ABC123" in line for line in lines)


def test_flash_with_timeout(tmp_path):
    """A command that finishes within flash_timeout is unaffected by it."""
    build = _make_build_dir(tmp_path)
    with serve(Zephyr(flash_command="echo within-timeout", flash_timeout=5)) as client:
        lines = _flash(client, build)
    assert "within-timeout" in "\n".join(lines)


@pytest.mark.parametrize(
    "command",
    [
        "echo started; sleep 30",
        # closes its output early: the deadline must also bound waiting for its exit
        "echo started; exec >/dev/null 2>&1; sleep 30",
    ],
)
def test_flash_timeout_stops_the_command(tmp_path, command):
    """A command still running at flash_timeout is stopped and the flash fails."""
    build = _make_build_dir(tmp_path)
    start = time.monotonic()
    with (
        serve(Zephyr(flash_command=command, flash_timeout=1)) as client,
        _raises_flash_failure("timed out after 1s"),
    ):
        _flash(client, build)
    assert time.monotonic() - start < 10


def test_flash_timeout_does_not_need_gnu_timeout(tmp_path, monkeypatch):
    """The deadline is enforced by the driver: no `timeout` executable is used,
    which macOS does not ship. A broken one first on the PATH changes nothing."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "timeout").write_text("#!/bin/sh\nexit 127\n")
    (fake_bin / "timeout").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    build = _make_build_dir(tmp_path)
    with serve(Zephyr(flash_command="echo within-timeout", flash_timeout=5)) as client:
        lines = _flash(client, build)
    assert "within-timeout" in "\n".join(lines)


def test_flash_propagates_failure(tmp_path):
    """A non-zero flash command must surface as an error to the client."""
    build = _make_build_dir(tmp_path)
    with (
        serve(Zephyr(flash_command="echo nope; exit 7", flash_timeout=0)) as client,
        _raises_flash_failure("rc=7"),
    ):
        _flash(client, build)


def test_missing_artifacts_fails_fast(tmp_path):
    """No artifacts under build_dir/zephyr => client errors before the exporter."""
    empty = tmp_path / "build"
    (empty / "zephyr").mkdir(parents=True)
    with serve(Zephyr(flash_command="true", flash_timeout=0)) as client, pytest.raises(FileNotFoundError):
        _flash(client, empty)


def _jmp_options(argv: list[str]) -> list[str]:
    """The part of a twister argv that jumpstarter adds."""
    return argv[:7]


def test_twister_args_passed_through_unchanged():
    """Twister arguments follow jumpstarter's own options verbatim, including
    dash-prefixed values and anything after `--`."""
    args = ["-p", "plat", "-T", "tests", "--test-only", "-x", "fixture", "--pytest-args=--map=/srv/map.yml"]
    argv = ZephyrClient._twister_argv("console", [*args, "--", "--flash-command"])
    assert argv[:2] == ["west", "twister"]
    assert argv[7:] == [*args, "--", "--flash-command"]


def test_twister_jumpstarter_options():
    """Jumpstarter adds device testing, its serial command and its flash wrapper,
    and nothing else: --test-only is up to the caller."""
    argv = ZephyrClient._twister_argv("console", [])
    assert argv == _jmp_options(argv)
    assert "--device-testing" in argv
    assert "--test-only" not in argv
    # Twister splits this on spaces/commas without a shell.
    assert re.split("[, ]", argv[argv.index("--device-serial-pty") + 1]) == [
        "j",
        "zephyr",
        "console",
        "pipe",
        "-i",
        "--retry-count",
        "10",
        "--retry-interval",
        "0.5",
    ]
    # Twister inserts its own flags after the first word: --driver goes after them.
    script, *extra = argv[argv.index("--flash-command") + 1].split(",")
    assert extra == ["--driver", "zephyr"]
    assert script.endswith("jmp_flash_wrapper.sh")
    assert os.access(script, os.X_OK)


def test_twister_passes_its_own_name(monkeypatch):
    """The driver can have any name in the exporter: twister() hands its own
    name to both wrappers."""
    seen: list[list[str]] = []
    monkeypatch.setattr(ZephyrClient, "_run_twister", lambda self, argv: seen.append(argv) or 0)
    board = Zephyr(flash_command="true", children={"console": PySerial(url="loop://")})
    with serve(Composite(children={"flash": board})) as root:
        cast(ZephyrClient, root.children["flash"]).twister(["-p", "plat"])
    argv = seen[0]
    assert argv[argv.index("--device-serial-pty") + 1].startswith("j,flash,console,pipe,")
    assert argv[argv.index("--flash-command") + 1].endswith(",--driver,flash")


@pytest.mark.parametrize("option", sorted(RESERVED_TWISTER_OPTIONS))
@pytest.mark.parametrize("form", ["separate", "equals"])
def test_twister_rejects_reserved_options(option, form):
    args = [option, "value"] if form == "separate" else [f"{option}=value"]
    with pytest.raises(ValueError, match=option):
        ZephyrClient._twister_argv("console", ["-p", "plat", *args])


def test_twister_accepts_lookalike_options():
    """Only exact names are reserved: twister disables abbreviations."""
    args = ["--device-serial-baud", "115200", "--device-flash-timeout=120", "--pytest-args=--hardware-map=x"]
    assert ZephyrClient._twister_argv("console", args)[7:] == args


def test_serial_child_name_found():
    with serve(Zephyr(flash_command="true", children={"uart": PySerial(url="loop://")})) as client:
        assert cast(ZephyrClient, client)._serial_child_name() == "uart"


def test_serial_child_name_missing():
    with serve(Zephyr(flash_command="true")) as client, pytest.raises(RuntimeError, match="No PySerial child"):
        cast(ZephyrClient, client)._serial_child_name()


def test_serial_child_name_several_needs_console():
    children: dict[str, Driver] = {"uart": PySerial(url="loop://"), "debug": PySerial(url="loop://")}
    with serve(Zephyr(flash_command="true", children=children)) as client:
        zephyr = cast(ZephyrClient, client)
        with pytest.raises(RuntimeError, match="--console"):
            zephyr._serial_child_name()
        assert zephyr._serial_child_name("debug") == "debug"
        with pytest.raises(ValueError, match="not a PySerial child"):
            zephyr._serial_child_name("nope")


class _RecordingSerial(PySerial):
    """PySerial that records release()/acquire() calls."""

    def __init__(self, *args, calls: list[str], **kwargs):
        super().__init__(*args, **kwargs)
        self._calls = calls

    async def release(self):
        self._calls.append(f"release:{self.url}")
        await super().release()

    async def acquire(self):
        self._calls.append(f"acquire:{self.url}")
        await super().acquire()


def test_flash_releases_serial_children_whatever_their_name(tmp_path):
    """Every PySerial child is released around the flash, not just one named `console`."""
    build = _make_build_dir(tmp_path)
    calls: list[str] = []
    uart = _RecordingSerial(url="loop://", calls=calls)
    debug = _RecordingSerial(url="loop://#debug", calls=calls)
    children: dict[str, Driver] = {"uart": uart, "debug": debug}
    with serve(Zephyr(flash_command="true", flash_timeout=0, release_consoles=True, children=children)) as client:
        _flash(client, build)
    assert sorted(c for c in calls if c.startswith("release")) == ["release:loop://", "release:loop://#debug"]
    assert sorted(c for c in calls if c.startswith("acquire")) == ["acquire:loop://", "acquire:loop://#debug"]
    assert calls.index("acquire:loop://") > calls.index("release:loop://#debug")
    assert not uart._released and not debug._released


def test_flash_reacquires_serial_after_failure(tmp_path):
    build = _make_build_dir(tmp_path)
    calls: list[str] = []
    uart = _RecordingSerial(url="loop://", calls=calls)
    with (
        serve(
            Zephyr(flash_command="exit 3", flash_timeout=0, release_consoles=True, children={"uart": uart})
        ) as client,
        _raises_flash_failure("rc=3"),
    ):
        _flash(client, build)
    assert calls == ["release:loop://", "acquire:loop://"]
    assert not uart._released


def test_flash_keeps_serial_open_by_default(tmp_path):
    """Without release_consoles, the console stays open so no board output is lost."""
    build = _make_build_dir(tmp_path)
    calls: list[str] = []
    uart = _RecordingSerial(url="loop://", calls=calls)
    with serve(Zephyr(flash_command="true", flash_timeout=0, children={"uart": uart})) as client:
        _flash(client, build)
    assert calls == []


def _alive(pid: int) -> bool:
    """True while ``pid`` runs.

    Asks the kernel first (signal 0 only checks the process exists), which works
    on any POSIX system. Where /proc exists (Linux), a zombie counts as dead:
    once its parent is gone it waits for init to reap it, which a container's
    PID 1 may never do. Same check as the Shell driver's tests.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return True  # no /proc (e.g. macOS): the kernel said it exists
    except ProcessLookupError:
        return False  # reaped after the file was opened: reading it then fails with ESRCH
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


@pytest.mark.anyio
async def test_interrupted_flash_kills_the_flash_command(tmp_path):
    """Closing the command stream mid-flash (client gone, Ctrl-C, cancellation)
    kills the command and everything it spawned: nothing is left holding the
    probe or the tty."""
    pid_file = tmp_path / "pid"
    # A background child, so the test covers the whole process group, not just bash.
    cmd = ["bash", "-c", f"sleep 60 & echo $! > {pid_file}; echo started; wait"]
    driver = Zephyr(flash_command="true")
    lines = driver._stream_cmd(cmd, cwd=str(tmp_path))
    async for line in lines:
        if line == "started":
            break
    pid = int(pid_file.read_text())
    assert _alive(pid)
    await lines.aclose()
    with anyio.fail_after(10):
        while _alive(pid):
            await anyio.sleep(0.05)


@pytest.mark.anyio
async def test_interrupted_flash_kills_children_that_outlive_the_command(tmp_path):
    """A child that ignores SIGTERM is killed too, even though the command
    itself exits on SIGTERM: it must not keep holding the probe or the tty."""
    pid_file = tmp_path / "pid"
    cmd = ["bash", "-c", f"(trap '' TERM; exec sleep 60) & echo $! > {pid_file}; echo started; wait"]
    driver = Zephyr(flash_command="true")
    lines = driver._stream_cmd(cmd, cwd=str(tmp_path))
    async for line in lines:
        if line == "started":
            break
    pid = int(pid_file.read_text())
    assert _alive(pid)
    await lines.aclose()
    with anyio.fail_after(10):
        while _alive(pid):
            await anyio.sleep(0.05)


def _invoke_twister(monkeypatch, cli_args: list[str], rc: int = 0, children: dict[str, Driver] | None = None):
    """Run `j zephyr twister <cli_args>` with twister stubbed; return (result, argv)."""
    seen: list[list[str]] = []

    def fake_run(self, argv):
        seen.append(argv)
        return rc

    monkeypatch.setattr(ZephyrClient, "_run_twister", fake_run)
    if children is None:
        children = {"console": PySerial(url="loop://")}
    with serve(Zephyr(flash_command="true", children=children)) as client:
        result = CliRunner().invoke(client.cli(), ["twister", *cli_args])
    return result, (seen[0] if seen else None)


@pytest.mark.parametrize("rc", [0, 1, 3])
def test_twister_cli_exits_with_twister_code(monkeypatch, rc):
    """`j zephyr twister` exits with twister's code, so CI sees failed tests."""
    result, _ = _invoke_twister(monkeypatch, ["-p", "plat"], rc=rc)
    assert result.exit_code == rc, result.output


def test_twister_cli_passes_unknown_options_through(monkeypatch):
    cli_args = ["-p", "plat", "-T", "tests", "--test-only", "-vv", "-O", "out", "--pytest-args=--foo=bar"]
    result, argv = _invoke_twister(monkeypatch, cli_args)
    assert result.exit_code == 0, result.output
    assert argv is not None and argv[7:] == cli_args


@pytest.mark.parametrize(
    "cli_args",
    [
        # twister's `--` (extra arguments for the test binary) reaches it intact
        ["-p", "plat", "-T", "tests", "--", "--seed", "1"],
        ["--", "--seed", "1"],
        ["-p", "plat", "--", "--", "x"],
        # options after the first twister argument are twister's, even jumpstarter's names
        ["-p", "plat", "--help"],
        ["-p", "plat", "--console", "c"],
    ],
)
def test_twister_cli_passes_args_verbatim(monkeypatch, cli_args):
    result, argv = _invoke_twister(monkeypatch, cli_args)
    assert argv is not None, result.output
    assert argv[7:] == cli_args


def test_twister_cli_reserved_option_after_double_dash_is_allowed(monkeypatch):
    """Arguments after twister's `--` belong to the test binary and are not checked."""
    cli_args = ["-p", "plat", "--", "--flash-command"]
    result, argv = _invoke_twister(monkeypatch, cli_args)
    assert result.exit_code == 0, result.output
    assert argv is not None and argv[7:] == cli_args


def test_twister_cli_help(monkeypatch):
    """A leading --help is jumpstarter's and does not run twister."""
    result, argv = _invoke_twister(monkeypatch, ["--help"])
    assert result.exit_code == 0, result.output
    assert argv is None
    assert "[TWISTER_ARGS]..." in result.output
    assert "--console" in result.output


@pytest.mark.parametrize("console_args", [["--console", "debug"], ["--console=debug"]])
def test_twister_cli_console_option(monkeypatch, console_args):
    children: dict[str, Driver] = {"uart": PySerial(url="loop://"), "debug": PySerial(url="loop://")}
    result, argv = _invoke_twister(monkeypatch, [*console_args, "-p", "plat"], children=children)
    assert result.exit_code == 0, result.output
    assert argv is not None and argv[argv.index("--device-serial-pty") + 1].startswith("j,zephyr,debug,pipe,")
    assert argv[7:] == ["-p", "plat"]


def test_twister_cli_console_missing_value(monkeypatch):
    result, argv = _invoke_twister(monkeypatch, ["--console"])
    assert result.exit_code == 2
    assert "requires an argument" in result.output
    assert argv is None


def test_twister_cli_rejects_reserved_option(monkeypatch):
    result, argv = _invoke_twister(monkeypatch, ["-p", "plat", "--flash-command", "my.sh"])
    assert result.exit_code == 2
    assert "--flash-command cannot be used" in result.output
    assert argv is None


def test_flash_cli(tmp_path, capfd):
    build = _make_build_dir(tmp_path)
    with serve(Zephyr(flash_command="echo FAKE-FLASH", flash_timeout=0)) as client:
        result = CliRunner().invoke(client.cli(), ["flash", "--build-dir", str(build), "--board-id", "ABC"])
    assert result.exit_code == 0, result.output
    # The lines may bypass CliRunner's capture under pytest; read the fds instead.
    out = result.output + capfd.readouterr().out
    assert "FAKE-FLASH" in out
    assert "Flash complete" in out


def test_flash_cli_failure_exits_non_zero(tmp_path, capfd):
    """twister's flash step relies on the command failing."""
    build = _make_build_dir(tmp_path)
    with serve(Zephyr(flash_command="echo boom; exit 7", flash_timeout=0)) as client:
        result = CliRunner().invoke(client.cli(), ["flash", "--build-dir", str(build)])
    assert result.exit_code == 1, result.output
    # A plain error message, not a traceback. It may bypass CliRunner's
    # capture under pytest; read the fds too.
    captured = capfd.readouterr()
    assert "Error: flash command failed (rc=7):" in result.output + captured.out + captured.err
    assert isinstance(result.exception, SystemExit)


def _run_wrapper(tmp_path: Path, wrapper: str, args: list[str]) -> list[str]:
    """Run a wrapper with a fake `j` that records its arguments; return them."""
    log = tmp_path / "j.log"
    fake_j = tmp_path / "j"
    fake_j.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > {log}\n')
    fake_j.chmod(0o755)
    script = Path(__file__).parent / wrapper
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    subprocess.run([str(script), *args], env=env, check=True)
    return log.read_text().splitlines()


@pytest.mark.parametrize(
    "args",
    [
        ["--build-dir", "/b/dir"],
        ["--build-dir", "/b/dir", "--board-id", "XDS110-1"],
    ],
)
def test_flash_wrapper_forwards_build_dir(tmp_path, args):
    """twister calls the wrapper with --build-dir (and maybe --board-id); only
    --build-dir reaches `j zephyr flash`."""
    assert _run_wrapper(tmp_path, "jmp_flash_wrapper.sh", args) == ["zephyr", "flash", "--build-dir", "/b/dir"]


@pytest.mark.parametrize(
    "args",
    [
        # as twister calls it: its flags first, then the words after the script
        ["--build-dir", "/b/dir", "--driver", "flash"],
        ["--build-dir", "/b/dir", "--board-id", "XDS110-1", "--driver", "flash"],
    ],
)
def test_flash_wrapper_uses_driver_name(tmp_path, args):
    assert _run_wrapper(tmp_path, "jmp_flash_wrapper.sh", args) == ["flash", "flash", "--build-dir", "/b/dir"]


def _make_archive(tmp_path: Path) -> Path:
    archive = tmp_path / "firmware.tar"
    hex_file = tmp_path / "zephyr.hex"
    hex_file.write_text(":00000001FF\n")
    with tarfile.open(archive, "w") as tar:
        tar.add(hex_file, arcname="zephyr.hex")
    return archive


def test_flash_archive_from_url(tmp_path):
    """An http(s) archive is downloaded by the exporter, not relayed by the client."""
    archive = _make_archive(tmp_path)
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/{archive.name}"
            with serve(Zephyr(flash_command="test -f {hex} && echo FROM-URL", flash_timeout=0)) as client:
                statuses = list(cast(ZephyrClient, client).flash_stream(url))
        finally:
            server.shutdown()
    assert "FROM-URL" in [s.stdout for s in statuses]
    received = [s for s in statuses if s.phase == FlashPhase.DOWNLOAD][-1]
    assert received.bytes_transferred == archive.stat().st_size
    assert statuses[-1].phase == FlashPhase.COMPLETE
