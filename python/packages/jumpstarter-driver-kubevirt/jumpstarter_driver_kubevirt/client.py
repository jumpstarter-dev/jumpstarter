from __future__ import annotations

import os
import sys
import termios
import tty

import click
from anyio import EndOfStream, create_task_group, wait_readable
from anyio._backends._asyncio import BrokenResourceError
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_network.client import NetworkClient
from jumpstarter_driver_power.client import PowerClient


class KubevirtPowerClient(PowerClient):
    pass


class ConsoleExit(Exception):
    pass


class KubevirtConsoleClient(NetworkClient):

    def cli(self):
        base = super().cli()

        @base.command()
        def pipe():
            """Interactive serial console (Ctrl+B x3 to exit)"""
            click.echo("Connected to KubeVirt serial console (Ctrl+B x3 to exit)", err=True)
            old_settings = None
            if sys.stdin.isatty():
                old_settings = termios.tcgetattr(sys.stdin.fileno())
                tty.setraw(sys.stdin.fileno())
            try:
                self.portal.call(self._pipe_console)
            except (KeyboardInterrupt, ConsoleExit):
                pass
            finally:
                if old_settings:
                    termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
                click.echo("\nDisconnected.", err=True)

        return base

    async def _pipe_console(self):
        async with self.stream_async(method="connect") as stream:
            try:
                async with create_task_group() as tg:
                    tg.start_soon(self._stdin_to_stream, stream)
                    await self._stream_to_stdout(stream)
                    tg.cancel_scope.cancel()
            except* ConsoleExit:
                pass

    @staticmethod
    async def _stream_to_stdout(stream):
        try:
            while True:
                data = await stream.receive()
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
        except (EndOfStream, BrokenResourceError):
            pass

    @staticmethod
    async def _stdin_to_stream(stream):
        fd = sys.stdin.fileno()
        os.set_blocking(fd, False)
        ctrl_b_count = 0
        try:
            while True:
                await wait_readable(fd)
                data = os.read(fd, 1024)
                if not data:
                    break
                for byte in data:
                    if byte == 0x02:  # Ctrl+B
                        ctrl_b_count += 1
                        if ctrl_b_count >= 3:
                            raise ConsoleExit
                    else:
                        ctrl_b_count = 0
                await stream.send(data)
        except (EndOfStream, BrokenResourceError, OSError):
            pass
        except ConsoleExit:
            raise
        finally:
            os.set_blocking(fd, True)


class KubevirtStorageClient(CompositeClient):
    def flash(self, image_url: str) -> None:
        self.call("flash", image_url)


class KubevirtClient(CompositeClient):
    pass
