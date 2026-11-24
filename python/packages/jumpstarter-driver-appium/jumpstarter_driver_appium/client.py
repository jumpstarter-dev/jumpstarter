import json
import os
import ssl
import tempfile
import zipfile
from collections.abc import Callable, Generator, Mapping
from concurrent.futures import CancelledError
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

import anyio
import click
from anyio import BrokenResourceError, EndOfStream
from anyio.abc import ObjectStream
from jumpstarter_driver_network.adapters import TcpPortforwardAdapter

from .common import AppiumTarget, check_app_name
from jumpstarter.client import DriverClient
from jumpstarter.streams.progress import ProgressAttribute

MAX_CA_BYTES = 1024 * 1024


def _validate_public_ca(certificate: Any) -> str:
    if not isinstance(certificate, str) or len(certificate) > MAX_CA_BYTES or "PRIVATE KEY" in certificate:
        raise ValueError("Appium info must contain only a public CA certificate")
    try:
        certificate.encode("ascii")
        ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=certificate)
    except (ValueError, UnicodeError, ssl.SSLError) as exc:
        raise ValueError("Appium info contains an invalid public CA certificate") from exc
    return certificate


def _integers(value: Any) -> Any:
    """Generic RPC values carry every number as a float; restore the integers."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _integers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_integers(item) for item in value]
    return value


def _is_cancelled(exc: BaseException) -> bool:
    # Waits run in a worker thread, where get_cancelled_exc_class() cannot see the
    # backend, so identify each backend's cancellation exception by name.
    name, module = type(exc).__name__, type(exc).__module__
    return (
        isinstance(exc, CancelledError)
        or (name == "CancelledError" and module.startswith("asyncio"))
        or (name == "Cancelled" and module.startswith("trio"))
    )


def _wait_for_interrupt(client: DriverClient) -> None:
    """Wait in the portal so cancelling `jmp shell` unwinds the listener."""
    try:
        client.portal.call(anyio.sleep_forever)
    except (KeyboardInterrupt, SystemExit, GeneratorExit, RuntimeError):
        return
    except BaseException as exc:
        if not _is_cancelled(exc):
            raise


@contextmanager
def _forward_endpoint(**kwargs):
    # Raise caller errors outside the adapter's task group to preserve their type.
    caller_error = None
    with TcpPortforwardAdapter(**kwargs) as addr:
        try:
            yield addr
        except BaseException as exc:  # noqa: BLE001 - re-raised outside the task group
            caller_error = exc
    if caller_error is not None:
        raise caller_error


@dataclass(kw_only=True)
class _FileStream(ObjectStream[bytes]):
    """Read a local file for ``resource_async``."""

    path: Path
    _file: Any = field(default=None, init=False)

    async def receive(self) -> bytes:
        if self._file is None:
            self._file = await anyio.open_file(self.path, "rb")
        chunk = await self._file.read(65536)
        if not chunk:
            raise EndOfStream
        return chunk

    async def send(self, item: bytes):
        raise BrokenResourceError("read-only stream")

    async def send_eof(self):
        pass

    async def aclose(self):
        if self._file is not None:
            await self._file.aclose()
            self._file = None

    @property
    def extra_attributes(self) -> Mapping[Any, Callable[[], Any]]:
        total = float(self.path.stat().st_size)
        return {ProgressAttribute.total: lambda: total}


@contextmanager
def _application_file(path: Path) -> Generator[Path, None, None]:
    """Yield an uploadable file; an ``.app`` bundle is zipped, as Appium accepts."""
    if path.is_file():
        yield path
        return
    if not path.is_dir() or path.suffix != ".app":
        raise ValueError(f"{path} is not an application file or .app bundle")
    with tempfile.TemporaryDirectory(prefix="jumpstarter-appium-") as directory:
        archive = Path(directory) / f"{path.name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for item in sorted(path.rglob("*")):
                if item.is_symlink() or item.is_file():
                    bundle.write(item, Path(path.name) / item.relative_to(path))
        yield archive


class _AppiumGroup(click.Group):
    """Keep arguments after `--` opaque to Click."""

    def parse_args(self, ctx, args):
        if "--" in args:
            separator = args.index("--")
            command = args[separator + 1 :]
            args = args[:separator]
            if not command:
                raise click.UsageError("Provide a command after --", ctx)
            ctx.meta["appium_command"] = command
        return super().parse_args(ctx, args)


class AppiumClient(DriverClient):
    """Open Appium sessions on a leased target, wherever its driver runs Appium."""

    def info(self) -> dict:
        """Start the target's Appium if needed and describe it."""
        return _integers(self.call("info"))

    @contextmanager
    def serve(self, *, port: int = 0) -> Generator[AppiumTarget, None, None]:
        """Expose the target's Appium on a loopback HTTPS port for the length of the block.

        ``port=0`` picks an unused port. Closing the block removes the listener
        and the CA file; the exporter keeps Appium running until ``stop()``,
        power off, reset or the end of the lease.
        """
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError("port must be an integer between 0 and 65535")
        info = self.info()
        certificate = _validate_public_ca(info.get("ca_certificate"))
        capabilities = info.get("capabilities")
        if not isinstance(capabilities, dict):
            raise TypeError("Appium info must contain a capabilities object")
        with tempfile.TemporaryDirectory(prefix="jumpstarter-appium-") as directory:
            ca_file = Path(directory) / "ca.pem"
            ca_file.write_text(certificate, encoding="ascii")
            ca_file.chmod(0o600)
            with _forward_endpoint(client=self, method="connect", local_host="127.0.0.1", local_port=port) as addr:
                yield AppiumTarget(
                    url=f"https://{addr[0]}:{addr[1]}",
                    default_capabilities=capabilities,
                    ca_file=ca_file,
                    uploader=self.upload_app,
                )

    def upload_app(self, path: str | os.PathLike) -> str:
        """Upload an application for this lease and return the token to use as ``appium:app``.

        Accepts ``.apk``, ``.aab``, ``.apks``, ``.ipa`` and ``.zip`` files and
        ``.app`` bundles, which are zipped first. The token also works as the
        ``app`` argument of ``mobile: installApp``. Uploads last until reset or
        the end of the lease.
        """
        with _application_file(Path(path)) as file:
            check_app_name(file.name)
            stream = _FileStream(path=file)
            with self.portal.wrap_async_context_manager(self.resource_async(stream)) as handle:
                return self.call("upload_app", handle, file.name)

    def stop(self) -> None:
        """Stop the target's Appium; the next session starts a fresh one."""
        self.call("stop")

    def cli(self):
        @click.group(cls=_AppiumGroup, invoke_without_command=True)
        @click.option("-P", "--port", type=click.IntRange(0, 65535), default=0, help="Local HTTPS port (0=auto)")
        @click.pass_context
        def appium(ctx, port):
            """Serve the target's Appium until Ctrl+C, or run: appium -- COMMAND."""
            if ctx.invoked_subcommand is not None:
                if "appium_command" in ctx.meta:
                    raise click.UsageError("Use appium -- COMMAND without a subcommand", ctx)
                return
            try:
                with self.serve(port=port) as target:
                    command = ctx.meta.get("appium_command")
                    if command is None:
                        click.echo(
                            json.dumps(
                                {
                                    "url": target.url,
                                    "ca_file": str(target.ca_file),
                                    "capabilities": target.capabilities(),
                                }
                            )
                        )
                        click.echo("Press Ctrl+C to stop", err=True)
                        _wait_for_interrupt(self)
                        return
                    result = self.portal.call(
                        partial(
                            anyio.run_process,
                            list(command),
                            env=target.environment(),
                            stdin=None,
                            stdout=None,
                            stderr=None,
                            check=False,
                        )
                    )
            except (TypeError, ValueError, RuntimeError, OSError) as exc:
                raise click.ClickException(str(exc)) from exc
            ctx.exit(result.returncode if result.returncode >= 0 else 128 - result.returncode)

        @appium.command()
        @click.argument("path", type=click.Path(exists=True, path_type=Path))
        def upload(path):
            """Upload an application and print the token to use as appium:app."""
            try:
                click.echo(self.upload_app(path))
            except (ValueError, RuntimeError, OSError) as exc:
                raise click.ClickException(str(exc)) from exc

        @appium.command()
        def stop():
            """Stop the target's Appium."""
            self.stop()

        return appium
