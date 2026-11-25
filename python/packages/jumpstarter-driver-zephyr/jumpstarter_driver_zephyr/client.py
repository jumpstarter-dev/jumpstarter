import shlex
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import click
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_pyserial.client import PySerialClient

from .driver import ARTIFACT_FILES
from jumpstarter.client.flasher import FlashPhase, FlashStatus, StreamingFlasherClient

# How twister's serial command waits for the console while the previous test's
# connection is still being released on the exporter.
SERIAL_RETRY_COUNT = 10
SERIAL_RETRY_INTERVAL = 0.5

# Twister options that `j zephyr twister` rejects, with the reason shown to the user.
# Jumpstarter sets how twister flashes the board and reaches its serial port.
RESERVED_TWISTER_OPTIONS = {
    "--flash-command": "jumpstarter flashes the leased board itself",
    "--device-serial-pty": "jumpstarter connects twister to the leased board's console",
    "--device-serial": "jumpstarter connects twister to the leased board's console",
    "--hardware-map": "the lease selects the board",
    "--generate-hardware-map": "the lease selects the board",
    "--west-flash": "it is ignored: jumpstarter's --flash-command takes precedence",
    "--west-runner": "it is ignored: jumpstarter's --flash-command takes precedence",
    "--west-flash-cmd": "it is ignored: jumpstarter's --flash-command takes precedence",
}


@dataclass(kw_only=True)
class ZephyrClient(StreamingFlasherClient, CompositeClient):
    """Client for flashing Zephyr firmware on a remote exporter.

    Collects the fixed-name firmware artifacts from a Zephyr build directory and
    streams them to the exporter, which runs its configured flash command. The
    client knows nothing about the probe or flash command - that lives in the
    exporter's driver config.

    Flashing comes from :class:`~jumpstarter.client.flasher.StreamingFlasherClient`:
    :meth:`flash_stream` sends a tar archive (a local path, or an http(s) URL
    the exporter downloads itself) and yields
    :class:`~jumpstarter.client.flasher.FlashStatus` updates; :meth:`flash`
    does the same and returns the final one.

    Designed to back twister's ``--flash-command``: :meth:`twister` points
    twister at ``jmp_flash_wrapper.sh``, which calls ``j zephyr flash`` (i.e.
    :meth:`flash_build_dir`) against the lease the surrounding ``jmp shell``
    established.
    """

    def flash_build_dir(
        self,
        build_dir: str,
        board_id: str | None = None,
    ) -> Iterator[FlashStatus]:
        """Flash firmware from a local Zephyr build directory.

        Collects the fixed-name artifacts (``zephyr.hex`` and, when present,
        ``zephyr.bin`` / ``zephyr.elf``) from ``<build_dir>/zephyr/``, tars them,
        and streams them to the exporter.

        Args:
            build_dir: Local Zephyr build directory (the ``--build-dir`` twister
                passes; artifacts live under its ``zephyr/`` subdir).
            board_id: Optional board/probe id (forwarded by twister). Unused for
                selector-based leases where one lease maps to one board; accepted
                so the call signature matches twister's flash-command contract.

        Yields:
            Progress updates, in real time. Each output line of the flash
            command arrives in the ``stdout`` field of a ``STEP`` update.

        Raises:
            RuntimeError: The flash failed on the exporter.
        """
        zephyr_dir = Path(build_dir) / "zephyr"
        artifacts = [(name, zephyr_dir / name) for name in ARTIFACT_FILES.values() if (zephyr_dir / name).is_file()]
        if not artifacts:
            raise FileNotFoundError(
                f"No firmware artifacts found under {zephyr_dir} "
                f"(expected one of {list(ARTIFACT_FILES.values())}). "
                "Is build_dir a Zephyr build directory?"
            )

        if board_id:
            yield FlashStatus(
                phase=FlashPhase.STEP, message=f"board-id: {board_id} (informational; lease selects the board)"
            )
        yield FlashStatus(phase=FlashPhase.STEP, message=f"Uploading: {', '.join(name for name, _ in artifacts)}")

        with tempfile.NamedTemporaryFile(suffix=".tar", delete=True) as tmp:
            # dereference: an artifact that is a symlink (accepted by is_file()
            # above) is archived as its target, not as a link the exporter
            # cannot resolve.
            with tarfile.open(tmp.name, "w", dereference=True) as tar:
                for name, path in artifacts:
                    tar.add(path, arcname=name)
            try:
                yield from self.flash_stream(tmp.name)
            except ExceptionGroup as group:
                raise _single_exception(group) from None

    @staticmethod
    def _flash_command(driver: str) -> str:
        """Build twister's --flash-command value.

        Twister reads it as comma-separated words and inserts its own flags
        right after the first one, so --driver goes after the script.
        """
        return f"{Path(__file__).parent.resolve() / 'jmp_flash_wrapper.sh'},--driver,{driver}"

    @staticmethod
    def _serial_pty_command(console: str, driver: str) -> str:
        """Build twister's --device-serial-pty value: ``j <driver> <console> pipe``.

        Twister runs it with stdin and stdout on a PTY and splits it on
        spaces/commas without a shell, so the console and driver names must not
        contain either. ``-i``: stdin is a tty, which pipe does not take as
        input on its own. The retries cover the console still being held by
        the previous test's connection.
        """
        return ",".join(
            [
                "j",
                driver,
                console,
                "pipe",
                "-i",
                "--retry-count",
                str(SERIAL_RETRY_COUNT),
                "--retry-interval",
                str(SERIAL_RETRY_INTERVAL),
            ]
        )

    def _serial_child_name(self, console: str | None = None) -> str:
        """Name of the PySerial child used as twister's serial port.

        Args:
            console: Child to use. When omitted, the driver must have exactly
                one PySerial child.
        """
        names = [name for name, child in self.children.items() if isinstance(child, PySerialClient)]
        if console is not None:
            if console not in names:
                raise ValueError(
                    f"{console!r} is not a PySerial child (PySerial children: {', '.join(names) or 'none'})"
                )
            return console
        if not names:
            raise RuntimeError("No PySerial child found to use as twister's serial port")
        if len(names) > 1:
            raise RuntimeError(f"Multiple PySerial children found ({', '.join(names)}): choose one with --console")
        return names[0]

    @staticmethod
    def _check_twister_args(args: list[str]) -> None:
        """Reject twister options that would bypass the leased board.

        Jumpstarter sets how twister flashes the board and reaches its serial
        port. Any of these options would point twister at something else, or be
        silently ignored because ``--flash-command`` takes precedence. Twister
        disables option abbreviations, so exact names are enough. Arguments
        after a bare ``--`` are not twister options and are not checked.
        """
        for arg in args:
            if arg == "--":
                break
            name = arg.split("=", 1)[0]
            if name in RESERVED_TWISTER_OPTIONS:
                raise ValueError(f"{name} cannot be used with j zephyr twister: {RESERVED_TWISTER_OPTIONS[name]}")

    @staticmethod
    def _twister_argv(console: str, args: list[str], driver: str = "zephyr") -> list[str]:
        """Build the twister command line.

        Jumpstarter's own options come first, so that they precede any ``--``
        in ``args``; ``args`` follow verbatim.

        Args:
            console: Name of the PySerial child twister talks to
                (``--device-serial-pty``).
            args: Arguments passed to twister unchanged.
            driver: Name of this driver in the exporter, so that both
                commands reach it with ``j <driver> ...``.
        """
        ZephyrClient._check_twister_args(args)
        return [
            "west",
            "twister",
            "--device-testing",
            "--device-serial-pty",
            ZephyrClient._serial_pty_command(console, driver),
            "--flash-command",
            ZephyrClient._flash_command(driver),
            *args,
        ]

    def _run_twister(self, argv: list[str]) -> int:
        """Run twister in the current directory, output inherited."""
        click.echo("Running: " + shlex.join(argv), err=True)
        return subprocess.call(argv)

    def twister(self, args: list[str], console: str | None = None) -> int:
        """Run twister against the leased board.

        Twister runs in the current directory with ``args`` passed unchanged,
        so a CI job can reuse the arguments of its ``--build-only`` step and
        add ``--test-only``. Jumpstarter only adds ``--device-testing``,
        ``--device-serial-pty`` and ``--flash-command``.

        Args:
            args: Twister arguments, e.g.
                ``["-p", "<platform>", "-T", "tests", "--test-only"]``. Options that select another way to flash or
                reach the board are rejected (see ``RESERVED_TWISTER_OPTIONS``).
            console: PySerial child to use as the board's serial port; required
                only when the driver has more than one.

        Returns:
            Twister's exit code. It is non-zero when twister itself fails, but
            also when any test fails, so a CI job that wants to collect the
            reports must not stop on it.
        """
        # This driver's name in the exporter config (what follows `j`), set by
        # core on every client. Only a root driver has none.
        driver = self.labels.get("jumpstarter.dev/name", "zephyr")
        argv = self._twister_argv(self._serial_child_name(console), list(args), driver)
        rc = self._run_twister(argv)
        click.echo(f"twister exited with {rc}", err=True)
        return rc

    def cli(self):
        # Build on the composite group so children (e.g. `j zephyr console ...`)
        # stay reachable: twister's serial command relies on it.
        base = CompositeClient.cli(self)
        base.help = "Flash Zephyr firmware on the exporter."

        @base.command()
        @click.option(
            "--build-dir",
            required=True,
            type=click.Path(exists=True, file_okay=False, dir_okay=True),
            help="Zephyr build directory (artifacts read from its zephyr/ subdir).",
        )
        @click.option(
            "--board-id",
            default=None,
            help="Board/probe id (forwarded by twister; informational here).",
        )
        def flash(build_dir, board_id):
            """Flash firmware from a Zephyr build directory."""
            self.logger.info("Flashing from build directory %s...", build_dir)
            try:
                _stream_to_stdout(self.flash_build_dir(build_dir, board_id=board_id))
            except RuntimeError as e:
                raise click.ClickException(str(e)) from e

        @base.command(cls=_TwisterCommand)
        @click.option(
            "--console",
            default=None,
            help="PySerial child to use as the board's serial port (only needed if there are several).",
        )
        def twister(console, args):
            """Run twister against the leased board.

            Jumpstarter's options must come first. Everything from the first
            other argument on is TWISTER_ARGS, passed to `west twister`
            unchanged, including any `--`; e.g. `j zephyr twister -p <platform>
            --help` shows twister's help. Twister runs in the current directory.

            Jumpstarter adds --device-testing, --device-serial-pty and
            --flash-command; options that would flash or reach the board another
            way are rejected.

            Exits with twister's exit code, which is also non-zero when a test
            fails.
            """
            try:
                rc = self.twister(list(args), console=console)
            except ValueError as e:
                raise click.UsageError(str(e)) from e
            except RuntimeError as e:
                raise click.ClickException(str(e)) from e
            # Exit with twister's own code, so CI sees failed tests.
            if rc != 0:
                click.get_current_context().exit(rc)

        return base


class _TwisterCommand(click.Command):
    """`j zephyr twister`: parse jumpstarter's leading options only.

    Click's parser always consumes the first bare `--` as its own end of
    options, but twister needs it: whatever follows is passed to the test
    binary. So only the leading `--console` and help options are left to click,
    and everything from the first other argument on is handed to twister
    verbatim, as the `args` parameter.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        help_names = set(ctx.help_option_names)
        i = 0
        while i < len(args):
            arg = args[i]
            if arg in help_names or arg.startswith("--console="):
                i += 1
            elif arg == "--console":
                i += 2  # a missing value is reported by click
            else:
                break
        super().parse_args(ctx, args[:i])
        ctx.params["args"] = tuple(args[i:])
        return []

    def collect_usage_pieces(self, ctx: click.Context) -> list[str]:
        return [*super().collect_usage_pieces(ctx), "[TWISTER_ARGS]..."]


def _single_exception(group: BaseExceptionGroup) -> BaseException:
    """The one exception inside ``group``, or ``group`` itself if it holds several.

    The upload runs in a task group, so a failure reported by the exporter
    reaches the client wrapped in one or more exception groups.
    """
    exc: BaseException = group
    while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
        exc = exc.exceptions[0]
    return exc


def _render(status: FlashStatus) -> str:
    """One plain line per update: the flash command's own output verbatim,
    otherwise the message, with the byte count while receiving."""
    if status.stdout is not None:
        return status.stdout
    if status.bytes_transferred is not None:
        total = f"/{status.bytes_total}" if status.bytes_total is not None else ""
        return f"{status.message} ({status.bytes_transferred}{total} bytes)"
    return status.message


def _stream_to_stdout(statuses: Iterator[FlashStatus]) -> None:
    """Print each update as it arrives, flushing so the user sees live progress.

    The ``ERROR`` update is not printed: the client raises it, and the caller
    reports it.
    """
    for status in statuses:
        if status.phase == FlashPhase.ERROR:
            continue
        click.echo(_render(status))
        sys.stdout.flush()
