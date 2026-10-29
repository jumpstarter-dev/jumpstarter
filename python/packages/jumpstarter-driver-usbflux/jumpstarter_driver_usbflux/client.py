import asyncio
import os
from collections.abc import Generator, Sequence
from concurrent.futures import CancelledError
from contextlib import contextmanager
from functools import partial

import anyio
import click

from .adapters import CONNECT_TIMEOUT, UsbfluxAdapter, forward_endpoint, validate_port
from jumpstarter.client import DriverClient


def is_cancelled(exc: BaseException) -> bool:
    # These waits run in a worker thread, where get_cancelled_exc_class() cannot
    # discover the backend. Trio is optional, so identify its exception by name.
    return isinstance(exc, (asyncio.CancelledError, CancelledError)) or (
        type(exc).__name__ == "Cancelled" and type(exc).__module__.startswith("trio")
    )


def wait_for_interrupt(client: DriverClient) -> None:
    """Wait in the portal so cancelling `jmp shell` unwinds the listeners."""
    try:
        client.portal.call(anyio.sleep_forever)
    except (KeyboardInterrupt, SystemExit, GeneratorExit, RuntimeError):
        return
    except BaseException as exc:
        if not is_cancelled(exc):
            raise


@contextmanager
def cli_errors():
    try:
        yield
    except (TypeError, ValueError, RuntimeError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


class CommandGroup(click.Group):
    """Keep arguments after `--` opaque to Click and to subcommand dispatch."""

    meta_key = "tool_command"

    def parse_args(self, ctx, args):
        if "--" in args:
            separator = args.index("--")
            command = args[separator + 1 :]
            args = args[:separator]
            if not command:
                raise click.UsageError("Provide a command after --", ctx)
            ctx.meta[self.meta_key] = command
        return super().parse_args(ctx, args)


class UsbFluxClient(DriverClient):
    """Hand one leased usbmux device to local tools: directly, via usbfluxd, or per command."""

    def info(self) -> dict:
        info = self.call("info")
        # Generic RPC values carry every number as a float; the daemon's device ID is an integer.
        device_id = info.get("device_id")
        if isinstance(device_id, float) and device_id.is_integer():
            info["device_id"] = int(device_id)
        return info

    def _require_present(self) -> dict:
        info = self.info()
        if info.get("present") is False:
            raise RuntimeError(info.get("reason") or "The usbmux device is not present")
        return info

    @contextmanager
    def serve(self, *, port: int = 0) -> Generator[str, None, None]:
        """Serve usbmux on IPv4 loopback; ``port=0`` selects an unused local port.

        The yielded endpoint is ``127.0.0.1:port``, suitable for ``USBMUXD_SOCKET_ADDRESS``.
        """
        validate_port(port, ephemeral=True)
        self._require_present()
        with forward_endpoint(client=self, method="connect_usbmux", local_port=port) as endpoint:
            yield endpoint

    @contextmanager
    def connect(
        self, *, port: int = 0, usbfluxctl: str = "usbfluxctl", timeout: float = CONNECT_TIMEOUT
    ) -> Generator[str, None, None]:
        """Register the lease's usbmux endpoint with the user's usbfluxd until exit."""
        self._require_present()
        with UsbfluxAdapter(client=self, port=port, usbfluxctl=usbfluxctl, timeout=timeout) as target:
            yield target

    def run(self, command: Sequence[str]) -> int:
        """Run a local command with ``USBMUXD_SOCKET_ADDRESS`` set to a lease endpoint."""
        if not command:
            raise ValueError("Provide a command to run")
        with self.serve() as target:
            env = os.environ.copy()
            env["USBMUXD_SOCKET_ADDRESS"] = target
            # Lease cancellation must stop the process before closing its transport.
            result = self.portal.call(
                partial(anyio.run_process, list(command), env=env, stdin=None, stdout=None, stderr=None, check=False)
            )
            return result.returncode

    def cli(self):
        @click.group(cls=CommandGroup, invoke_without_command=True)
        @click.pass_context
        def usbflux(ctx):
            """One usbmux device. Run your own tool with: usbflux -- COMMAND."""
            command = ctx.meta.get(CommandGroup.meta_key)
            if command is not None:
                if ctx.invoked_subcommand is not None:
                    raise click.UsageError("Use usbflux -- COMMAND without a subcommand", ctx)
                with cli_errors():
                    returncode = self.run(command)
                ctx.exit(returncode if returncode >= 0 else 128 - returncode)
            if ctx.invoked_subcommand is None:
                click.echo(ctx.get_help())

        @usbflux.command()
        def info():
            """Show identity and presence."""
            for key, value in self.info().items():
                click.echo(f"{key}: {value}")

        @usbflux.command()
        @click.option("-P", "--port", type=click.IntRange(0, 65535), default=0, help="Local port (0=auto)")
        def serve(port):
            """Serve a local usbmux endpoint for your own tools until Ctrl+C."""
            with cli_errors(), self.serve(port=port) as target:
                click.echo(target)
                click.echo(f"export USBMUXD_SOCKET_ADDRESS={target}")
                click.echo("Press Ctrl+C to stop")
                wait_for_interrupt(self)

        @usbflux.command()
        @click.option("-P", "--port", type=click.IntRange(0, 65535), default=0, help="Local port (0=auto)")
        @click.option("--usbfluxctl", default="usbfluxctl", show_default=True, help="Path to your local usbfluxctl")
        def connect(port, usbfluxctl):
            """Attach to your own usbfluxd until Ctrl+C."""
            with cli_errors():
                with self.connect(port=port, usbfluxctl=usbfluxctl) as target:
                    click.echo(f"Connected at {target}")
                    click.echo("Press Ctrl+C to disconnect")
                    wait_for_interrupt(self)
                click.echo("Disconnected")

        return usbflux
