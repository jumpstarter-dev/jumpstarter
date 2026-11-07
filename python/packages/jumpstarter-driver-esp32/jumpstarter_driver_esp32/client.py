import json
import sys
from collections.abc import Generator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import anyio
import click
from jumpstarter_driver_opendal.client import FlasherClient
from opendal import Operator

from jumpstarter.client.adapters import blocking
from jumpstarter.client.core import DriverMethodNotImplemented
from jumpstarter.client.flasher import FlashStatus, PathBuf, _AsyncIteratorStream, _http_url_adapter, _parse_path
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
    ):
        """Flash one or more images in a single esptool session.

        ``path`` is an image, an ``{address: image}`` mapping, or an ESP-IDF
        build directory (or its ``flasher_args.json``). A single image is
        written at ``target``, 0x0 by default.
        """
        if operator is not None:
            return super().flash(path, target=target, operator=operator, compression=compression)
        for _ in self.flash_stream(path, target=target, compression=compression):
            pass

    def flash_stream(
        self,
        path: PathBuf | dict[str, PathBuf],
        *,
        target: str | None = None,
        compression: Compression | None = None,
    ) -> Generator[FlashStatus, None, None]:
        """Like ``flash``, yielding progress updates as the images are written."""
        images = _resolve_images(path, target)
        # The exporter fetches a URL itself, so a URL cannot share an upload
        # with other images and gets a session of its own.
        if any(_parse_path(image)[1] is not None for _, image in images):
            groups = [[image] for image in images]
        else:
            groups = [images]
        try:
            for group in groups:
                yield from self._flash_group(group, compression)
        # An upload's task group wraps errors raised while it is open, hence except*.
        except* DriverMethodNotImplemented:
            # The exporter predates flash_images: one call, and one session, per image.
            super().flash({hex(address): image for address, image in images}, compression=compression)

    def _flash_group(
        self, images: list[tuple[int, PathBuf]], compression: Compression | None
    ) -> Generator[FlashStatus, None, None]:
        url = _parse_path(images[0][1])[1]
        if url is not None:
            layout = [{"address": images[0][0], "size": None, "name": url.rsplit("/", 1)[-1]}]
            upload = _http_url_adapter(client=self, url=url, mode="rb")
        else:
            paths = [_existing_file(image) for _, image in images]
            layout = [
                {"address": address, "size": file.stat().st_size, "name": file.name}
                for (address, _), file in zip(images, paths, strict=True)
            ]
            upload = _upload_files(client=self, paths=paths, compression=compression)
        with upload as handle:
            for value in self.streamingcall("flash_images", handle, layout):
                yield FlashStatus.model_validate(value)

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
        def flash(file, address, targets, compression):
            """Flash firmware to ESP32

            FILE is an image, a URL, or an ESP-IDF build directory: its
            flasher_args.json lists the images and their offsets.
            """
            if bool(file) == bool(targets):
                raise click.UsageError("Give either FILE or --target/-t")
            if targets and address is not None:
                raise click.UsageError("--address applies to FILE; give each --target its own address")
            path = _parse_target_specs(targets) if targets else file
            show = _StatusPrinter()
            for status in self.flash_stream(path, target=address, compression=compression):
                show(status)

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


class _StatusPrinter:
    """Print flash progress one line at a time, every 10% of an image."""

    def __init__(self):
        self._last: tuple[int, int] | None = None

    def __call__(self, status: FlashStatus):
        if status.step_index is None:
            _echo(status.message)
            return
        prefix = f"[{status.step_index}/{status.total_steps}] {status.step_name}:"
        if status.progress is None:
            _echo(f"{prefix} {status.message}")
            return
        key = (status.step_index, int(status.progress * 10))
        if key != self._last:
            self._last = key
            _echo(f"{prefix} {status.message} {status.progress:.0%}")


def _echo(line: str):
    # The upload's progress bar is still live while the statuses arrive. rich
    # keeps it in place by redirecting sys.stdout, which click.echo bypasses
    # unless it is handed sys.stdout explicitly.
    click.echo(line, file=sys.stdout)


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


def _parse_address(value: str | int) -> int:
    if isinstance(value, int):
        return value
    try:
        return int(value, 0)
    except ValueError:
        raise ArgumentError(f"invalid flash address {value!r}") from None


def _existing_file(path: PathBuf) -> Path:
    file = Path(path).resolve()
    if not file.is_file():
        raise ArgumentError(f"{path}: no such file")
    return file


def _idf_flasher_args(path: PathBuf) -> Path | None:
    """The flasher_args.json of an ESP-IDF build that ``path`` names, if it names one."""
    if _parse_path(path)[1] is not None:
        return None
    local = Path(path)
    if local.is_dir():
        flasher_args = local / "flasher_args.json"
        if not flasher_args.is_file():
            raise ArgumentError(f"{path} is a directory without flasher_args.json")
        return flasher_args
    return local if local.name == "flasher_args.json" else None


def _resolve_images(path: PathBuf | dict[str, PathBuf], target: str | None) -> list[tuple[int, PathBuf]]:
    """List the (address, image) pairs to write."""
    if isinstance(path, dict):
        if target is not None:
            raise ArgumentError("'target' parameter is not valid when flashing multiple images")
        return [(_parse_address(address), image) for address, image in path.items()]
    flasher_args = _idf_flasher_args(path)
    if flasher_args is not None:
        if target is not None:
            raise ArgumentError("'target' parameter is not valid for an ESP-IDF build")
        files = json.loads(flasher_args.read_text())["flash_files"]
        return [(_parse_address(address), flasher_args.parent / file) for address, file in files.items()]
    return [(_parse_address(target or 0), path)]


@blocking
@asynccontextmanager
async def _upload_files(*, client, paths: list[Path], compression: Compression | None):
    """Send local files back to back as one resource, under one progress bar."""

    async def chunks():
        for path in paths:
            async with await anyio.open_file(path, "rb") as f:
                while chunk := await f.read(65536):
                    yield chunk

    stream = _AsyncIteratorStream(iterator=chunks(), total=sum(path.stat().st_size for path in paths))
    async with client.resource_async(stream, content_encoding=compression) as handle:
        yield handle
