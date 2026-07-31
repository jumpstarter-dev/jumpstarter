import logging
import sys
import time
from contextlib import contextmanager
from typing import Optional

import click
from anyio import BrokenResourceError, EndOfStream, create_task_group, open_file, sleep, to_thread
from anyio.streams.file import FileReadStream
from jumpstarter_driver_network.adapters import PexpectAdapter
from pexpect.fdpexpect import fdspawn

from .console import Console, ConsoleStreamDrop
from jumpstarter.client import DriverClient
from jumpstarter.client.decorators import driver_click_group

logger = logging.getLogger(__name__)

KNOWN_POWER_CLIENTS = frozenset({
    "jumpstarter_driver_power.client.PowerClient",
    "jumpstarter_driver_power.client.VirtualPowerClient",
    "jumpstarter_driver_ridesx.client.RideSXPowerClient",
    "jumpstarter_driver_noyito_relay.client.NoyitoPowerClient",
    "jumpstarter_driver_snmp.client.SNMPServerClient",
})


class PySerialClient(DriverClient):
    """
    A client for handling serial communication using pexpect.

    """

    def open(self) -> fdspawn:
        """
        Open a pexpect session. You can find the pexpect documentation
        here: https://pexpect.readthedocs.io/en/stable/api/pexpect.html#spawn-class

        Returns:
            fdspawn: The pexpect session object.
        """
        return self.stack.enter_context(self.pexpect())

    @contextmanager
    def pexpect(self):
        """
        Create a pexpect adapter context manager.

        Yields:
            PexpectAdapter: The pexpect adapter object.
        """
        with PexpectAdapter(client=self) as adapter:
            yield adapter

    async def _pipe_serial(
        self,
        output_file: Optional[str] = None,
        input_enabled: bool = False,
        append: bool = False,
        no_output: bool = False,
    ):
        """
        Pipe serial port data to stdout or a file, optionally reading from stdin.

        Args:
            output_file: Path to output file. If None, writes to stdout.
            input_enabled: If True, also pipe stdin to serial port.
            append: If True, append to file instead of overwriting.
            no_output: If True, do not read serial output; only forward stdin to serial.
        """
        async with self.stream_async(method="connect") as stream:
            # Fire-and-forget mode: only forward stdin and exit when stdin reaches EOF.
            if no_output:
                if input_enabled:
                    bytes_read, bytes_sent = await self._stdin_to_serial(stream)
                    if bytes_read != bytes_sent:
                        raise RuntimeError(
                            f"stdin forwarding incomplete: read {bytes_read} bytes but sent {bytes_sent} bytes"
                        )
                return

            async with create_task_group() as tg:
                # Input task: stdin -> serial (optional)
                if input_enabled:
                    tg.start_soon(self._stdin_to_serial, stream)

                # Output runs inline - when the stream ends, we cancel and exit
                await self._serial_to_output(stream, output_file, append)
                tg.cancel_scope.cancel()

    async def _serial_to_output(self, stream, output_file: Optional[str], append: bool):
        """Read from serial and write to file or stdout."""
        try:
            if output_file:
                mode = "ab" if append else "wb"
                async with await open_file(output_file, mode) as f:
                    while True:
                        data = await stream.receive()
                        await f.write(data)
                        await f.flush()
            else:
                while True:
                    data = await stream.receive()
                    sys.stdout.buffer.write(data)
                    sys.stdout.buffer.flush()
        except EndOfStream:
            click.echo("\nSerial connection closed normally (end of stream).", err=True)
        except BrokenResourceError:
            click.echo(
                "\nSerial connection lost (broken resource). The connection may have been interrupted.", err=True
            )

    async def _stdin_to_serial(self, stream) -> tuple[int, int]:
        """Read from stdin and write to serial. Returns (bytes_read, bytes_sent)."""
        stdin = FileReadStream(sys.stdin.buffer)
        bytes_read = 0
        bytes_sent = 0
        try:
            while True:
                data = await stdin.receive(max_bytes=1024)
                if not data:
                    # EOF on stdin, just stop reading but keep serial output running
                    break
                bytes_read += len(data)
                await stream.send(data)
                bytes_sent += len(data)
        except EndOfStream:
            # EOF on stdin, just stop reading but keep serial output running
            pass

        # Signal write completion for streams that support half-close.
        if hasattr(stream, "send_eof"):
            try:
                await stream.send_eof()
            except (AttributeError, BrokenResourceError, EndOfStream):
                pass

        return bytes_read, bytes_sent

    def _find_power_client(self):
        # Check if hotkey is disabled
        method_label = self.labels.get("jumpstarter.dev/pyserial/power-control-method", "cycle")
        if not method_label:
            return None

        root = getattr(self, 'root', None)
        if root is None:
            return None

        # Explicit ref takes precedence
        ref_label = self.labels.get("jumpstarter.dev/pyserial/power-control-ref")
        if ref_label:
            power_client = root.children.get(ref_label)
            if power_client is None:
                logger.warning(
                    "power_control_ref '%s' not found in DUT tree — power cycle hotkey disabled",
                    ref_label
                )
                return None
            return power_client

        # Auto-discovery: collect all power-capable clients
        candidates = []
        self._collect_power_clients(root, candidates)

        if len(candidates) == 0:
            return None
        if len(candidates) == 1:
            return candidates[0]

        # Multiple candidates — ambiguous
        names = [c.labels.get("jumpstarter.dev/name", "unknown") for c in candidates]
        logger.warning(
            "Multiple power drivers found (%s) — power cycle hotkey disabled. "
            "Set power_control_ref to select one explicitly.",
            ", ".join(names)
        )
        return None

    def _collect_power_clients(self, client, result, seen_uuids=None):
        if seen_uuids is None:
            seen_uuids = set()
        client_uuid = getattr(client, 'uuid', None)
        if client_uuid and client_uuid in seen_uuids:
            return
        if client_uuid:
            seen_uuids.add(client_uuid)
        client_class = client.labels.get("jumpstarter.dev/client")
        if client_class in KNOWN_POWER_CLIENTS:
            result.append(client)
        for child in client.children.values():
            self._collect_power_clients(child, result, seen_uuids)

    def _make_power_cycle(self, power_client):
        method_label = self.labels.get("jumpstarter.dev/pyserial/power-control-method", "cycle")
        methods = [m for m in method_label.split(",") if m]

        # Pre-validate and parse all steps
        steps = []
        for method in methods:
            if method.startswith("sleep:"):
                try:
                    delay = float(method.split(":", 1)[1])
                    steps.append(("sleep", delay))
                except (ValueError, IndexError):
                    logger.warning("Invalid sleep step '%s' — power cycle hotkey disabled", method)
                    return None
            else:
                operation = getattr(power_client, method, None)
                if callable(operation):
                    steps.append(("operation", operation))
                else:
                    logger.warning(
                        "Power client does not have callable method '%s' — power cycle hotkey disabled",
                        method
                    )
                    return None

        async def _cycle():
            for kind, value in steps:
                if kind == "sleep":
                    await sleep(value)
                else:
                    await to_thread.run_sync(value)

        return _cycle

    def cli(self):  # noqa: C901
        @driver_click_group(self)
        def base():
            """Serial port client"""
            pass

        @base.command()
        def start_console():
            """Start serial port console"""
            power_client = self._find_power_client()
            on_power_cycle = self._make_power_cycle(power_client) if power_client is not None else None
            click.echo("\nStarting serial port console ... exit with CTRL+B x 3 times\n")
            if on_power_cycle is not None:
                click.echo("Power cycle: CTRL+] x 3 times\n")
            retries = 0
            while retries < 30:
                console = Console(serial_client=self, on_power_cycle=on_power_cycle)
                try:
                    console.run()
                    break
                except ConsoleStreamDrop:
                    click.echo("\r\nSerial connection lost, reconnecting...\n", err=True)
                    retries += 1
                    time.sleep(1)
            else:
                click.echo("\nSerial connection lost (reconnect attempts exhausted).\n", err=True)

        @base.command()
        @click.option(
            "-o", "--output",
            type=click.Path(),
            default=None,
            help="Output file path. If not specified, writes to stdout.",
        )
        @click.option(
            "-i", "--input",
            "input_flag",
            is_flag=True,
            default=None,
            help="Force enable stdin to serial port. Auto-detected if stdin is piped.",
        )
        @click.option(
            "--no-input",
            is_flag=True,
            default=False,
            help="Disable stdin to serial port, even if stdin is piped.",
        )
        @click.option(
            "-a", "--append",
            is_flag=True,
            default=False,
            help="Append to output file instead of overwriting.",
        )
        @click.option(
            "--no-output",
            is_flag=True,
            default=False,
            help="Disable serial output handling. Send stdin to serial and exit at EOF.",
        )
        def pipe(output, input_flag, no_input, append, no_output):  # noqa: C901
            """Pipe serial port data to stdout or file.

            By default, reads from the serial port and writes to stdout.
            Automatically detects if stdin is piped and enables bidirectional mode.

            When stdin is used, commands are sent until EOF, then continues
            monitoring serial output until Ctrl+C.

            Use -o/--output to write to a file instead.
            Use -i/--input to force enable stdin to serial (auto-detected).
            Use --no-input to disable stdin even when piped.
            Use --no-output for fire-and-forget input (send stdin to serial and exit).

            Exit with Ctrl+C.

            Examples:

              j serial pipe                # Log serial output to stdout

              j serial pipe -o serial.log  # Log serial output to a file

              echo "hello" | j serial pipe # Send to serial, continue monitoring

              cat commands.txt | j serial pipe -o serial.log # Send commands, log output

              cat commands.txt | j serial pipe --no-output # Fire-and-forget: send and exit at EOF
            """
            if input_flag and no_input:
                raise click.UsageError("Cannot use both --input and --no-input")

            if no_output and output:
                raise click.UsageError("Cannot use both --no-output and --output")

            if no_output and append:
                raise click.UsageError("Cannot use both --no-output and --append")

            if append and not output:
                raise click.UsageError("--append requires --output")

            # Auto-detect stdin: if it's not a TTY (i.e., piped or redirected), enable input
            stdin_is_piped = not sys.stdin.isatty()

            # Determine if input should be enabled
            if no_input:
                input_enabled = False
            elif input_flag:
                input_enabled = True
            else:
                input_enabled = stdin_is_piped

            if no_output and not input_enabled:
                raise click.UsageError("--no-output requires stdin input (pipe stdin or use --input)")

            # Show appropriate status message
            if input_enabled and stdin_is_piped and not input_flag:
                mode_desc = "auto-detected piped stdin"
            elif input_enabled and input_flag:
                mode_desc = "forced input mode"
            elif input_enabled:
                mode_desc = "input enabled"
            else:
                mode_desc = "read-only"

            if no_output:
                click.echo(f"Fire-and-forget mode ({mode_desc}): stdin→serial, no output (exits at EOF)", err=True)
            elif not output and not input_enabled:
                click.echo(f"Reading from serial port ({mode_desc})... (Ctrl+C to exit)", err=True)
            elif not output and input_enabled:
                msg = f"Bidirectional mode ({mode_desc}): stdin→serial, serial→stdout (Ctrl+C to exit)"
                click.echo(msg, err=True)
            elif output and not input_enabled:
                click.echo(f"Logging serial output to {output} ({mode_desc})... (Ctrl+C to exit)", err=True)
            else:
                msg = f"Bidirectional mode ({mode_desc}) with logging to {output}... (Ctrl+C to exit)"
                click.echo(msg, err=True)

            try:
                self.portal.call(self._pipe_serial, output, input_enabled, append, no_output)
            except KeyboardInterrupt:
                click.echo("\nStopped.", err=True)

        return base
