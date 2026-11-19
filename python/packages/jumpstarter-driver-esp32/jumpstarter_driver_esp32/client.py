import json
import sys
from collections.abc import Generator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import click
from jumpstarter_driver_opendal.client import FlasherClient, clean_filename
from opendal import Operator

from jumpstarter.client.adapters import blocking
from jumpstarter.client.core import DriverMethodNotImplemented
from jumpstarter.client.flasher import FlashStatus, PathBuf, _AsyncIteratorStream, _parse_path
from jumpstarter.common.exceptions import ArgumentError
from jumpstarter.streams.encoding import Compression


@dataclass(kw_only=True)
class Esp32FlasherClient(FlasherClient):
    """Client interface for ESP32 flasher driver."""

    def flash(
        self,
        path: PathBuf | dict[str, PathBuf],
        *,
        target: str | None = None,
        operator: Operator | dict[str, Operator] | None = None,
        compression: Compression | None = None,
        skip_unchanged: bool = True,
    ):
        """Flash one or more images in a single esptool session.

        ``path`` is an image (a file or a URL), an ``{address: image}``
        mapping, or an ESP-IDF build directory. A single image is written at
        ``target``, 0x0 by default. With ``skip_unchanged``, images that
        already match the flash are not rewritten. It has no effect with
        ``operator``, which goes through the base flasher, one image per call.
        """
        if operator is not None:
            return super().flash(path, target=target, operator=operator, compression=compression)
        for _ in self.flash_stream(path, target=target, compression=compression, skip_unchanged=skip_unchanged):
            pass

    def flash_stream(
        self,
        path: PathBuf | dict[str, PathBuf],
        *,
        target: str | None = None,
        compression: Compression | None = None,
        skip_unchanged: bool = True,
    ) -> Generator[FlashStatus, None, None]:
        """Like ``flash``, yielding progress updates as the images are written."""
        images = _resolve_images(path, target)
        layout, upload = _layout(images)
        try:
            with _upload(client=self, data=upload) as handle:
                for value in self.streamingcall("flash_images", handle, layout, compression, skip_unchanged):
                    yield FlashStatus.model_validate(value)
        # The upload's task group wraps errors raised while it is open, hence except*.
        except* DriverMethodNotImplemented:
            # The exporter predates flash_images: one call, and one session, per image.
            super().flash({hex(address): image for address, image in images}, compression=compression)

    def get_chip_info(self) -> dict[str, str]:
        """Get chip information including name, features, and MAC address."""
        return self.call("get_chip_info")

    def erase(self):
        """Erase the entire flash memory."""
        return self.call("erase")

    def hard_reset(self):
        """Hard reset the ESP32 chip."""
        return self.call("hard_reset")

    def enter_bootloader(self):
        """Enter ESP32 download mode via DTR/RTS toggle."""
        return self.call("enter_bootloader")

    def cli(self):
        base = super().cli()

        # Override the inherited flash command to add --address
        base.commands.pop("flash", None)

        @base.command()
        @click.argument("file", required=False)
        @click.option("--address", "-a", default=None, help="Flash address for FILE (default 0x0)")
        @click.option(
            "--target",
            "-t",
            "targets",
            multiple=True,
            metavar="ADDRESS:FILE",
            help="Write FILE at ADDRESS; repeat to write several images in one session",
        )
        @click.option("--compression", type=click.Choice(Compression, case_sensitive=False))
        @click.option(
            "--skip-unchanged/--no-skip-unchanged",
            default=True,
            show_default=True,
            help="Leave images that already match the flash unwritten",
        )
        def flash(file, address, targets, compression, skip_unchanged):
            """Flash firmware to ESP32

            FILE is an image, a URL, or an ESP-IDF build directory: its
            flasher_args.json lists the images and their offsets.
            """
            if bool(file) == bool(targets):
                raise click.UsageError("Give either FILE or --target/-t")
            path = _parse_target_specs(targets) if targets else file
            for status in self.flash_stream(
                path, target=address, compression=compression, skip_unchanged=skip_unchanged
            ):
                # The upload's progress bar is still live while the statuses
                # arrive. rich keeps it in place by redirecting sys.stdout,
                # which click.echo bypasses unless it is handed sys.stdout.
                click.echo(_status_line(status), file=sys.stdout)

        @base.command()
        def chip_info():
            """Get chip info (name, features, MAC)"""
            info = self.get_chip_info()
            for key, value in info.items():
                click.echo(f"{key}: {value}")

        @base.command()
        def erase():
            """Erase entire flash"""
            self.erase()
            click.echo("done")

        @base.command()
        def reset():
            """Hard reset the chip"""
            self.hard_reset()
            click.echo("done")

        @base.command()
        def bootloader():
            """Enter download mode"""
            self.enter_bootloader()
            click.echo("done")

        return base


def _status_line(status: FlashStatus) -> str:
    line = status.message
    if status.step_index is not None:
        line = f"[{status.step_index}/{status.total_steps}] {status.step_name}: {line}"
    if status.progress is not None:
        line = f"{line} {status.progress:.0%}"
    return line


def _parse_target_specs(specs: tuple[str, ...]) -> dict[str, PathBuf]:
    images: dict[str, PathBuf] = {}
    for spec in specs:
        address, sep, file = spec.partition(":")
        if not sep or not file:
            raise click.UsageError(f"Invalid target '{spec}', expected ADDRESS:FILE")
        if address in images:
            raise click.UsageError(f"Address {address} is given more than once")
        images[address] = file
    return images


def _parse_address(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError:
        raise ArgumentError(f"invalid flash address {value!r}") from None


def _resolve_images(path: PathBuf | dict[str, PathBuf], target: str | None) -> list[tuple[int, PathBuf]]:
    """List the (address, image) pairs to write."""
    if isinstance(path, dict):
        images = path
    elif Path(path).is_dir():
        # An ESP-IDF build: flasher_args.json lists its images by address.
        try:
            flash_files = json.loads((Path(path) / "flasher_args.json").read_text())["flash_files"]
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise ArgumentError(f"{path}: cannot read flash_files from flasher_args.json: {e}") from None
        images = {address: Path(path) / file for address, file in flash_files.items()}
    else:
        return [(_parse_address(target or "0"), path)]
    if target is not None:
        raise ArgumentError("an address can only be given for a single image")
    return [(_parse_address(address), image) for address, image in images.items()]


def _layout(images: list[tuple[int, PathBuf]]) -> tuple[list[dict], bytes]:
    """Describe ``images`` for ``flash_images``, and gather the upload they need.

    Local files travel in the upload as they are on disk, compressed or not,
    and their entries give their size in it. URLs are left to the exporter to
    download, as the base flasher does.
    """
    layout = []
    upload = bytearray()
    for address, image in images:
        entry: dict = {"address": address, "name": clean_filename(image)}
        _, url = _parse_path(image)
        if url is None:
            data = Path(image).read_bytes()
            entry["size"] = len(data)
            upload += data
        else:
            entry["url"] = url
        layout.append(entry)
    return layout, bytes(upload)


@blocking
@asynccontextmanager
async def _upload(*, client, data: bytes):
    """Send ``data`` to the exporter as one resource, under one progress bar."""

    async def chunks():
        for start in range(0, len(data), 65536):
            yield data[start : start + 65536]

    async with client.resource_async(_AsyncIteratorStream(iterator=chunks(), total=len(data))) as handle:
        yield handle
