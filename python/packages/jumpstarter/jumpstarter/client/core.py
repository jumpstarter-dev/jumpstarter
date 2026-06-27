"""
Base classes for drivers and driver clients.

Driver-client calls route through the Rust core (``jumpstarter_core.ClientSession``) over the
lease transport — no Python gRPC. Values cross as plain JSON; the Rust core owns the proto
codec, router framing, and exception→status mapping.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import anyio
import jumpstarter_core
from anyio import create_task_group
from anyio.abc import ObjectStream

from jumpstarter.common import ExporterStatus, LogSource, Metadata
from jumpstarter.common.exceptions import JumpstarterException
from jumpstarter.common.jsonable import to_jsonable as _to_jsonable
from jumpstarter.common.resources import ResourceMetadata
from jumpstarter.streams.common import forward_stream
from jumpstarter.streams.metadata import MetadataStream
from jumpstarter.streams.progress import ProgressStream


class DriverError(JumpstarterException):
    """
    Raised when a driver call returns an error
    """


class DriverMethodNotImplemented(DriverError, NotImplementedError):
    """
    Raised when a driver method is not implemented
    """


class DriverInvalidArgument(DriverError, ValueError):
    """
    Raised when a driver method is called with invalid arguments
    """


def _map_ffi_error(method, exc):
    """Map a jumpstarter_core.DriverError to the client exception taxonomy."""
    message = f"{method}: {exc}"
    if isinstance(exc, (jumpstarter_core.DriverError.Unimplemented, jumpstarter_core.DriverError.NotFound)):
        return DriverMethodNotImplemented(message)
    if isinstance(exc, jumpstarter_core.DriverError.InvalidArgument):
        return DriverInvalidArgument(message)
    return DriverError(message)


class _FFIByteStream(ObjectStream[bytes]):
    """Wraps a jumpstarter_core.ClientByteStream as an anyio byte stream."""

    def __init__(self, chan):
        self._chan = chan

    async def send(self, item: bytes) -> None:
        await self._chan.write(bytes(item))

    async def receive(self) -> bytes:
        data = await self._chan.read()
        if data is None:
            raise anyio.EndOfStream
        return bytes(data)

    async def send_eof(self) -> None:
        # Half-close the send direction only, leaving the receive direction open. This matters
        # for resource *reads* (host→client): forward_stream's sink→channel copy hits immediate
        # EOF on the empty sink and calls send_eof; a full close here would tear down the
        # receive path the driver is still writing into (BrokenResourceError). The local bridge
        # exposes close_write; the Rust ClientByteStream currently only has a full shutdown.
        close_write = getattr(self._chan, "close_write", None)
        if close_write is not None:
            await close_write()
        else:
            await self._chan.shutdown()

    async def aclose(self) -> None:
        await self._chan.shutdown()


@dataclass(kw_only=True)
class AsyncDriverClient(Metadata):
    """
    Async driver client base class.

    Backing implementation of the blocking driver client. Driver calls route through the Rust
    core via ``session`` (a ``jumpstarter_core.ClientSession``).
    """

    # The Rust core client session (jumpstarter_core.ClientSession), set by client_from_session.
    session: Any = None

    log_level: str = "INFO"
    logger: logging.Logger = field(init=False)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(self.log_level)
        self._get_status_unsupported = False

        # add default handler
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
            self.logger.addHandler(handler)

    async def get_status_async(self) -> ExporterStatus | None:
        """Get the current exporter status, or None if the exporter does not implement it."""
        if self._get_status_unsupported:
            return None
        import json

        try:
            data = json.loads(await self.session.get_status())
        except jumpstarter_core.DriverError as e:
            if isinstance(e, jumpstarter_core.DriverError.Unimplemented):
                self._get_status_unsupported = True
                return None
            raise DriverError(f"Failed to get exporter status: {e}") from None
        return ExporterStatus.from_proto(data["status"])

    async def call_async(self, method, *args):
        """Make DriverCall by method name and arguments"""
        import json

        args_json = json.dumps([_to_jsonable(arg) for arg in args])
        try:
            result_json = await self.session.driver_call(str(self.uuid), method, args_json)
        except jumpstarter_core.DriverError as e:
            raise _map_ffi_error(method, e) from None
        return json.loads(result_json)

    async def streamingcall_async(self, method, *args):
        """Make StreamingDriverCall by method name and arguments"""
        import json

        args_json = json.dumps([_to_jsonable(arg) for arg in args])
        try:
            stream = await self.session.streaming_driver_call(str(self.uuid), method, args_json)
            while True:
                item = await stream.next()
                if item is None:
                    break
                yield json.loads(item)
        except jumpstarter_core.DriverError as e:
            raise _map_ffi_error(method, e) from None

    @asynccontextmanager
    async def stream_async(self, method):
        import json

        request = json.dumps({"uuid": str(self.uuid), "method": method})
        chan = await self.session.stream(request)
        metadata = json.loads(chan.initial_metadata())
        async with MetadataStream(stream=_FFIByteStream(chan), metadata=metadata) as stream:
            yield stream

    @asynccontextmanager
    async def resource_async(
        self,
        stream,
        content_encoding: str | None = None,
    ):
        import json

        # `content_encoding` reaches the Rust core via this request JSON; Rust owns the wire
        # codec entirely now (it always advertises `x_jmp_accept_encoding` for a supported
        # encoding and compresses the WRITE source on its client seam), so Python NEVER wraps
        # the stream — it forwards the caller's raw bytes as-is.
        request = json.dumps({"uuid": str(self.uuid), "x_jmp_content_encoding": content_encoding})
        chan = await self.session.stream(request)
        rstream = _FFIByteStream(chan)
        metadata = ResourceMetadata(**json.loads(chan.initial_metadata()))
        async with forward_stream(ProgressStream(stream=stream), rstream):
            # The handle crosses to the driver as the JSON-string form (not a dict): a driver's
            # resource param is typed `str` (parse_resource json.loads it), so over the native
            # per-interface wire it must encode into a proto `string` field. (The old Value codec
            # tolerated a dict; the typed native encoder does not.)
            yield metadata.resource.model_dump_json()

    @asynccontextmanager
    async def log_stream_async(self, show_all_logs: bool = True):
        def _emit(source, severity, message):
            is_hook = source in (LogSource.BEFORE_LEASE_HOOK, LogSource.AFTER_LEASE_HOOK)
            if not (is_hook or show_all_logs):
                return
            logger_name = {
                LogSource.BEFORE_LEASE_HOOK: "exporter:beforeLease",
                LogSource.AFTER_LEASE_HOOK: "exporter:afterLease",
                LogSource.DRIVER: "exporter:driver",
            }.get(source, "exporter:system")
            log_level = getattr(logging, severity or "INFO", logging.INFO)
            logging.getLogger(logger_name).log(log_level, message)

        async def log_stream_ffi():
            import json

            try:
                stream = await self.session.log_stream()
                while True:
                    item = await stream.next()
                    if item is None:
                        break
                    entry = json.loads(item)
                    src = entry.get("source")
                    try:
                        source = LogSource(src) if src is not None else LogSource.SYSTEM
                    except ValueError:
                        source = LogSource.SYSTEM
                    _emit(source, entry.get("severity"), entry.get("message", ""))
            except jumpstarter_core.DriverError as e:
                self.logger.debug("FFI log stream ended: %s", e)

        async with create_task_group() as tg:
            tg.start_soon(log_stream_ffi)
            try:
                yield
            finally:
                tg.cancel_scope.cancel()
