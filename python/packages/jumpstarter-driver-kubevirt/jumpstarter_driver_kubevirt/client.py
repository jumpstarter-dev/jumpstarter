from __future__ import annotations

import logging
import os
import sys
import termios
import tty
from uuid import uuid4

import click
from anyio import EndOfStream, create_task_group, wait_readable
from anyio._backends._asyncio import BrokenResourceError
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_network.client import NetworkClient
from jumpstarter_driver_opendal.adapter import OpendalAdapter
from jumpstarter_driver_opendal.client import operator_for_path
from jumpstarter_driver_opendal.common import PathBuf
from jumpstarter_driver_power.client import PowerClient
from opendal import Operator

from .errors import SUPERSEDED
from jumpstarter.client import DriverClient
from jumpstarter.client.decorators import driver_click_group
from jumpstarter.streams.encoding import Compression


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


class KubevirtStorageClient(DriverClient):
    def flash(
        self,
        path: PathBuf | dict[str, PathBuf],
        *,
        target: str | None = None,
        operator: Operator | dict[str, Operator] | None = None,
        compression: Compression | None = None,
    ) -> None:
        if isinstance(path, dict):
            if target is not None:
                raise ValueError("target cannot be combined with a disk mapping")
            images = path
        else:
            if isinstance(operator, dict):
                raise TypeError("operator mapping requires a disk mapping")
            images = {target or "root": path}
        if not images:
            raise ValueError("At least one image is required")
        self._check_sources(images, operator, compression)
        # The client owns the id: if begin's response is lost or the call is cancelled,
        # it can always recover the operation it just started.
        operation_id = uuid4().hex
        self.logger.info("Preparing %d disk(s): %s", len(images), ", ".join(images))
        try:
            self.call("begin_flash", operation_id, list(images))
            for i, (name, image) in enumerate(images.items(), start=1):
                self.logger.info("Uploading disk '%s' (%d/%d); this includes CDI conversion", name, i, len(images))
                op = operator.get(name) if isinstance(operator, dict) else operator
                self._stage_image(operation_id, name, image, op, compression)
            self.logger.info("Stopping the VM and switching to the new disk(s)")
            self.call("commit_flash", operation_id)
        except BaseException as error:
            if self._cleanup_after_failure(operation_id, error):
                self.logger.info("Flash complete; the VM is off until you power it on")
                return  # The activation succeeded, but its response was lost.
            raise
        self.logger.info("Flash complete; the VM is off until you power it on")

    def _cleanup_after_failure(self, operation_id, error) -> bool:
        """Clean up the interrupted operation; True when the activation already landed."""
        try:
            committed = self.call("recover_flash", operation_id)
        except Exception as cleanup_error:
            # A preempted flash has no leftovers of its own: whoever replaced the
            # operation already cleaned them up, so only a failed cleanup warns.
            if SUPERSEDED not in str(cleanup_error):
                logging.getLogger(__name__).warning(
                    "Flash interrupted; leftovers are cleaned up by the next flash or lease", exc_info=True
                )
            return False
        return committed and isinstance(error, Exception)

    def _check_sources(self, images, operator, compression) -> None:
        """Reject bad input before begin_flash provisions anything.

        The sources are only stat'ed, never opened: an object-storage image is
        presigned for just 60 seconds, so each source is opened right before
        its own upload.
        """
        for name, image in images.items():
            if isinstance(image, str) and image.startswith(("http://", "https://")):
                if compression is not None:
                    raise ValueError(f"Disk '{name}': --compression is not supported with an http(s) URL")
                continue
            op = operator.get(name) if isinstance(operator, dict) else operator
            if op is None:
                path, op, _ = operator_for_path(image)
            else:
                path = image
            try:
                op.stat(path)
            except Exception as error:
                raise ValueError(f"Disk '{name}': cannot read image {image}: {error}") from error

    def _stage_image(self, operation_id, name, image, operator, compression):
        original_url = image if isinstance(image, str) and image.startswith(("http://", "https://")) else None
        if operator is None:
            image, operator, _ = operator_for_path(image)
        # Open resources just before use: presigned URLs may have short lifetimes.
        with OpendalAdapter(
            client=self,
            operator=operator,
            path=image,
            mode="rb",
            compression=compression,
            original_url=original_url,
        ) as handle:
            self.call("stage_flash", operation_id, name, handle)

    def cli(self):
        @driver_click_group(self)
        def base():
            """Flash VM boot and data disks. Successful flashes leave the VM off."""

        @base.command()
        @click.argument("file", required=False)
        @click.option("--target", "-t", "targets", multiple=True, help="name:file (repeat for multiple disks)")
        @click.option("--compression", type=click.Choice(Compression, case_sensitive=False))
        def flash(file, targets, compression):
            if bool(file) == bool(targets):
                raise click.UsageError("Specify FILE or one or more --target name:file arguments")
            if file:
                self.flash(file, compression=compression)
                return
            images = {}
            for value in targets:
                name, separator, image = value.partition(":")
                if not separator or not name or not image or name in images:
                    raise click.UsageError("Each --target must have a distinct name and use name:file")
                images[name] = image
            self.flash(images, compression=compression)

        return base


class KubevirtClient(CompositeClient):
    pass
