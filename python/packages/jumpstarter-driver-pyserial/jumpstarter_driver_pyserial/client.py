import sys
from contextlib import contextmanager
from typing import Optional

import click
from anyio import EndOfStream, create_task_group, open_file, sleep
from anyio.streams.file import FileReadStream
from jumpstarter_driver_network.adapters import PexpectAdapter
from pexpect.fdpexpect import fdspawn

from .console import Console
from jumpstarter.client import DriverClient
from jumpstarter.client.decorators import driver_click_group


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
    ):
        """
        Pipe serial port data to stdout or a file, optionally reading from stdin.

        Args:
            output_file: Path to output file. If None, writes to stdout.
            input_enabled: If True, also pipe stdin to serial port.
            append: If True, append to file instead of overwriting.
        """
        async with self.stream_async(method="connect") as stream:
            async with create_task_group() as tg:
                # Output task: serial -> file/stdout
                tg.start_soon(self._serial_to_output, stream, output_file, append)

                # Input task: stdin -> serial (optional)
                if input_enabled:
                    tg.start_soon(self._stdin_to_serial, stream)

                # Keep running until interrupted (Ctrl+C)
                # When input is enabled, this continues even after stdin EOF
                while True:
                    await sleep(1)

    async def _serial_to_output(self, stream, output_file: Optional[str], append: bool):
        """Read from serial and write to file or stdout."""
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

    async def _stdin_to_serial(self, stream):
        """Read from stdin and write to serial. Returns when stdin reaches EOF."""
        stdin = FileReadStream(sys.stdin.buffer)
        try:
            while True:
                data = await stdin.receive(max_bytes=1024)
                if not data:
                    # EOF on stdin, just stop reading but keep serial output running
                    return
                await stream.send(data)
        except EndOfStream:
            # EOF on stdin, just stop reading but keep serial output running
            return

    def cli(self):  # noqa: C901
        @driver_click_group(self)
        def base():
            """Serial port client"""
            pass

        @base.command()
        def start_console():
            """Start serial port console"""
            click.echo("\nStarting serial port console ... exit with CTRL+B x 3 times\n")
            console = Console(serial_client=self)
            console.run()

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
        def pipe(output, input_flag, no_input, append):  # noqa: C901
            """Pipe serial port data to stdout or file.

            By default, reads from the serial port and writes to stdout.
            Automatically detects if stdin is piped and enables bidirectional mode.

            When stdin is used, commands are sent until EOF, then continues
            monitoring serial output until Ctrl+C.

            Use -o/--output to write to a file instead.
            Use -i/--input to force enable stdin to serial (auto-detected).
            Use --no-input to disable stdin even when piped.

            Exit with Ctrl+C.

            Examples:

              j serial pipe                # Log serial output to stdout

              j serial pipe -o serial.log  # Log serial output to a file

              echo "hello" | j serial pipe # Send to serial, continue monitoring

              cat commands.txt | j serial pipe -o serial.log # Send commands, log output
            """
            if append and not output:
                raise click.UsageError("--append requires --output")

            if input_flag and no_input:
                raise click.UsageError("Cannot use both --input and --no-input")

            # Auto-detect stdin: if it's not a TTY (i.e., piped or redirected), enable input
            stdin_is_piped = not sys.stdin.isatty()

            # Determine if input should be enabled
            if no_input:
                input_enabled = False
            elif input_flag:
                input_enabled = True
            else:
                input_enabled = stdin_is_piped

            # Show appropriate status message
            if input_enabled and stdin_is_piped and not input_flag:
                mode_desc = "auto-detected piped stdin"
            elif input_enabled and input_flag:
                mode_desc = "forced input mode"
            elif input_enabled:
                mode_desc = "input enabled"
            else:
                mode_desc = "read-only"

            if not output and not input_enabled:
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
                self.portal.call(self._pipe_serial, output, input_enabled, append)
            except KeyboardInterrupt:
                click.echo("\nStopped.", err=True)

        return base
