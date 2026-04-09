"""
Simplified FlasherClient that does not depend on opendal.

For local files: streams via the existing resource_async mechanism using anyio.
For HTTP URLs: passes a PresignedRequestResource directly to the exporter,
which already handles presigned downloads via aiohttp.
"""

from __future__ import annotations

from abc import ABCMeta, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from typing import Any, Callable, Mapping, cast

import click
from anyio import BrokenResourceError, EndOfStream
from anyio.abc import ObjectStream

from jumpstarter.client import DriverClient
from jumpstarter.client.adapters import blocking
from jumpstarter.client.decorators import driver_click_group
from jumpstarter.common.resources import PresignedRequestResource
from jumpstarter.streams.encoding import Compression
from jumpstarter.streams.progress import ProgressAttribute

PathBuf = str | PathLike


@dataclass(kw_only=True)
class _AsyncIteratorStream(ObjectStream[bytes]):
    """Wraps an async iterator as an ObjectStream for resource_async."""

    iterator: Any
    total: int | None = None

    async def receive(self) -> bytes:
        try:
            return await self.iterator.__anext__()
        except StopAsyncIteration:
            raise EndOfStream from None

    async def send(self, item: bytes):
        raise BrokenResourceError("read-only stream")

    async def send_eof(self):
        pass

    async def aclose(self):
        pass

    @property
    def extra_attributes(self) -> Mapping[Any, Callable[[], Any]]:
        if self.total is not None and self.total > 0:
            return {ProgressAttribute.total: lambda: float(self.total)}
        return {}


@dataclass(kw_only=True)
class _FileWriteObjectStream(ObjectStream[bytes]):
    """Wraps a file path as a writable ObjectStream for resource_async."""

    path: Path
    _file: Any = field(default=None, init=False)

    async def receive(self) -> bytes:
        raise EndOfStream

    async def send(self, item: bytes):
        if self._file is None:
            import anyio

            self._file = await anyio.open_file(self.path, "wb")
        await self._file.write(item)

    async def send_eof(self):
        if self._file is not None:
            await self._file.aclose()
            self._file = None

    async def aclose(self):
        if self._file is not None:
            await self._file.aclose()
            self._file = None


def _parse_path(path: PathBuf) -> tuple[Path | None, str | None]:
    """Parse a path into either a local Path or an HTTP URL.

    Returns (local_path, None) for local files, or (None, url) for HTTP URLs.
    """
    path_str = str(path)
    if path_str.startswith(("http://", "https://")):
        return None, path_str
    return Path(path).resolve(), None


@blocking
@asynccontextmanager
async def _local_file_adapter(
    *,
    client: DriverClient,
    path: Path,
    mode: str = "rb",
    compression: Compression | None = None,
):
    """Stream a local file via resource_async, without opendal."""
    import anyio

    if mode == "rb":
        # Read mode: stream file content to exporter
        file_size = path.stat().st_size

        async def file_reader():
            async with await anyio.open_file(path, "rb") as f:
                while True:
                    chunk = await f.read(65536)
                    if not chunk:
                        break
                    yield chunk

        stream = _AsyncIteratorStream(
            iterator=file_reader(),
            total=file_size,
        )

        async with client.resource_async(stream, content_encoding=compression) as res:
            yield res
    else:
        # Write mode: receive content from exporter into file
        stream = _FileWriteObjectStream(path=path)
        async with client.resource_async(stream, content_encoding=compression) as res:
            yield res


@blocking
@asynccontextmanager
async def _http_url_adapter(
    *,
    client: DriverClient,
    url: str,
    mode: str = "rb",
):
    """Create a PresignedRequestResource for an HTTP URL.

    The exporter already handles HTTP downloads via aiohttp,
    so we just pass the URL as a presigned GET request.
    """
    if mode == "rb":
        yield PresignedRequestResource(
            headers={},
            url=url,
            method="GET",
        ).model_dump(mode="json")
    else:
        yield PresignedRequestResource(
            headers={},
            url=url,
            method="PUT",
        ).model_dump(mode="json")


class FlasherClientInterface(metaclass=ABCMeta):
    @abstractmethod
    def flash(
        self,
        path: PathBuf | dict[str, PathBuf],
        *,
        target: str | None = None,
        compression: Compression | None = None,
    ):
        """Flash image to DUT"""
        ...

    @abstractmethod
    def dump(
        self,
        path: PathBuf,
        *,
        target: str | None = None,
        compression: Compression | None = None,
    ):
        """Dump image from DUT"""
        ...

    def cli(self):
        @driver_click_group(self)
        def base():
            """Generic flasher interface"""
            pass

        @base.command()
        @click.argument("file", nargs=-1, required=False)
        @click.option(
            "--target",
            "-t",
            "target_specs",
            multiple=True,
            help="name:file",
        )
        @click.option("--compression", type=click.Choice(Compression, case_sensitive=False))
        def flash(file, target_specs, compression):
            if target_specs:
                mapping: dict[str, str] = {}
                for spec in target_specs:
                    if ":" not in spec:
                        raise click.ClickException(f"Invalid target spec '{spec}', expected name:file")
                    name, img = spec.split(":", 1)
                    mapping[name] = img
                self.flash(cast(dict[str, PathBuf], mapping), compression=compression)
                return

            if not file:
                raise click.ClickException("FILE argument is required unless --target/-t is used")

            self.flash(file[0], target=None, compression=compression)

        @base.command()
        @click.argument("file")
        @click.option("--target", type=str)
        @click.option("--compression", type=click.Choice(Compression, case_sensitive=False))
        def dump(file, target, compression):
            """Dump image from DUT to file"""
            self.dump(file, target=target, compression=compression)

        return base


class FlasherClient(FlasherClientInterface, DriverClient):
    def _flash_single(
        self,
        image: PathBuf,
        *,
        target: str | None,
        compression: Compression | None,
    ):
        """Flash image to DUT"""
        local_path, url = _parse_path(image)

        if url is not None:
            # HTTP URL: pass as presigned request for exporter-side download
            with _http_url_adapter(client=self, url=url, mode="rb") as handle:
                return self.call("flash", handle, target)
        else:
            # Local file: stream via resource_async
            with _local_file_adapter(client=self, path=local_path, mode="rb", compression=compression) as handle:
                return self.call("flash", handle, target)

    def flash(
        self,
        path: PathBuf | dict[str, PathBuf],
        *,
        target: str | None = None,
        compression: Compression | None = None,
    ):
        if isinstance(path, dict):
            if target is not None:
                from jumpstarter.common.exceptions import ArgumentError

                raise ArgumentError("'target' parameter is not valid when flashing multiple images")

            results: dict[str, object] = {}
            for part, img in path.items():
                results[part] = self._flash_single(img, target=part, compression=compression)
            return results

        return self._flash_single(path, target=target, compression=compression)

    def dump(
        self,
        path: PathBuf,
        *,
        target: str | None = None,
        compression: Compression | None = None,
    ):
        """Dump image from DUT"""
        local_path, url = _parse_path(path)

        if url is not None:
            with _http_url_adapter(client=self, url=url, mode="wb") as handle:
                return self.call("dump", handle, target)
        else:
            with _local_file_adapter(client=self, path=local_path, mode="wb", compression=compression) as handle:
                return self.call("dump", handle, target)
