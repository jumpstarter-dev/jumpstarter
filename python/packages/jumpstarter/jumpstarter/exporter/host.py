"""In-process driver host adapter + a local in-process client bridge.

``DriverHost`` reuses the existing ``Driver`` classes + ``@export``/``@exportstream``
markers and crosses the boundary as plain JSON (the Rust core owns the proto-``Value``
codec, report assembly, gRPC and router framing). It implements the ``jumpstarter_core``
``DriverHost``/``DriverHostFactory`` protocols for the in-process exporter.

``LocalSession`` bridges that same ``DriverHost`` to the ``jumpstarter_core.ClientSession``
interface *without* a network/transport, so ``serve()`` runs driver tests through the same
FFI-shaped dispatch (no old Python exporter, no grpc). The ``jc.*`` types here are plain
data carriers — the local path makes no FFI runtime calls.

Streams are handle-based: ``streaming_open``/``open_stream`` register the iterator/byte
channel under an integer handle the caller drives by.
"""

from __future__ import annotations

import json
import logging
from inspect import isasyncgenfunction, iscoroutinefunction
from itertools import count
from typing import Any
from uuid import uuid4

import jumpstarter_core as jc
from anyio import to_thread

from jumpstarter.common.jsonable import to_jsonable as _to_jsonable
from jumpstarter.common.resources import ClientStreamResource
from jumpstarter.driver.base import SUPPORTED_CONTENT_ENCODINGS
from jumpstarter.driver.decorators import (
    MARKER_DRIVERCALL,
    MARKER_MAGIC,
    MARKER_STREAMCALL,
    MARKER_STREAMING_DRIVERCALL,
)
from jumpstarter.streams.common import create_memory_stream

_log = logging.getLogger("jumpstarter.exporter.host")


def _lookup(driver, name: str, marker: str):
    """Resolve an ``@export``/``@exportstream`` method by name, validating its marker."""
    method = getattr(driver, name, None)
    if method is None or getattr(method, marker, None) != MARKER_MAGIC:
        raise jc.DriverError.NotFound(f"method {name} not found on driver")
    return method


def _raise_mapped(exc: BaseException) -> None:
    """Map a driver exception to the typed ``DriverError`` (UniFFI panics on any undeclared
    exception, so this catch must be total)."""
    if isinstance(exc, jc.DriverError):
        raise exc
    if isinstance(exc, NotImplementedError):
        raise jc.DriverError.Unimplemented(str(exc))
    if isinstance(exc, ValueError):
        raise jc.DriverError.InvalidArgument(str(exc))
    if isinstance(exc, TimeoutError):
        raise jc.DriverError.DeadlineExceeded(str(exc))
    raise jc.DriverError.Unknown(str(exc))


class DriverHost:
    """A lease's driver host: the whole instantiated driver tree, dispatched by UUID."""

    def __init__(self, root):
        self._root = root
        self._by_uuid = {str(uuid): driver for (uuid, _p, _n, driver) in root.enumerate()}
        self._handles = count(1)
        self._result_streams: dict[int, tuple[str, Any]] = {}
        self._channels: dict[int, dict] = {}

    def _driver(self, uuid: str):
        driver = self._by_uuid.get(uuid)
        if driver is None:
            raise jc.DriverError.NotFound(f"unknown driver uuid: {uuid}")
        return driver

    async def describe(self) -> list[jc.DriverNode]:
        _log.debug("describe: introspecting driver tree")
        nodes = []
        for (uuid, parent, name, driver) in self._root.enumerate():
            labels = dict(driver.labels)
            labels.update(driver.extra_labels())
            labels["jumpstarter.dev/client"] = driver.client()
            if name:
                labels["jumpstarter.dev/name"] = name
            nodes.append(
                jc.DriverNode(
                    uuid=str(uuid),
                    parent_uuid=str(parent.uuid) if parent else None,
                    labels=labels,
                    description=driver.description or None,
                    methods_description=driver.methods_description or {},
                )
            )
        return nodes

    async def driver_call(self, uuid: str, method_name: str, args_json: str) -> str:
        _log.debug("driver_call uuid=%s method=%s", uuid, method_name)
        driver = self._driver(uuid)
        method = _lookup(driver, method_name, MARKER_DRIVERCALL)
        args = json.loads(args_json)
        try:
            if iscoroutinefunction(method):
                result = await method(*args)
            else:
                result = await to_thread.run_sync(method, *args)
        except BaseException as exc:  # noqa: BLE001 — must be total (see _raise_mapped)
            _raise_mapped(exc)
        return json.dumps(_to_jsonable(result))

    async def streaming_open(self, uuid: str, method_name: str, args_json: str) -> int:
        driver = self._driver(uuid)
        method = _lookup(driver, method_name, MARKER_STREAMING_DRIVERCALL)
        args = json.loads(args_json)
        try:
            if isasyncgenfunction(method):
                state = ("async", method(*args))
            else:
                state = ("sync", iter(method(*args)))
        except BaseException as exc:  # noqa: BLE001
            _raise_mapped(exc)
        handle = next(self._handles)
        self._result_streams[handle] = state
        return handle

    async def streaming_next(self, handle: int):
        kind, it = self._result_streams[handle]
        try:
            if kind == "async":
                try:
                    result = await it.__anext__()
                except StopAsyncIteration:
                    return None
            else:
                sentinel = object()

                def _next():
                    return next(it, sentinel)

                result = await to_thread.run_sync(_next)
                if result is sentinel:
                    return None
        except BaseException as exc:  # noqa: BLE001
            _raise_mapped(exc)
        return json.dumps(_to_jsonable(result))

    async def streaming_close(self, handle: int) -> None:
        state = self._result_streams.pop(handle, None)
        if state and state[0] == "async":
            aclose = getattr(state[1], "aclose", None)
            if aclose:
                try:
                    await aclose()
                except BaseException:  # noqa: BLE001
                    pass

    async def open_stream(self, request_json: str) -> jc.OpenStream:
        req = json.loads(request_json)
        uuid = req["uuid"]
        driver = self._driver(uuid)
        if "method" in req:
            # Driver @exportstream (console / serial / network).
            method = _lookup(driver, req["method"], MARKER_STREAMCALL)
            try:
                cm = method()
                stream = await cm.__aenter__()
            except BaseException as exc:  # noqa: BLE001
                _raise_mapped(exc)
            handle = next(self._handles)
            self._channels[handle] = {"cm": cm, "stream": stream}
            return jc.OpenStream(handle=handle, initial_metadata=[])

        # Resource stream (flash/dump): memory pipe + register the far end in the driver's
        # resource map (a later driver call reads it by uuid).
        remote, resource = create_memory_stream()
        resource_uuid = uuid4()
        driver.resources[resource_uuid] = resource
        encoding = req.get("x_jmp_content_encoding")
        resource_handle = ClientStreamResource(
            uuid=resource_uuid, x_jmp_content_encoding=encoding
        ).model_dump_json()
        metadata = [jc.MetadataEntry(key="resource", value=resource_handle)]
        if encoding in SUPPORTED_CONTENT_ENCODINGS:
            metadata.append(jc.MetadataEntry(key="x_jmp_accept_encoding", value=encoding))
        handle = next(self._handles)
        self._channels[handle] = {"cm": None, "stream": remote}
        return jc.OpenStream(handle=handle, initial_metadata=metadata)

    async def stream_read(self, handle: int):
        state = self._channels.get(handle)
        if state is None:  # closed concurrently (e.g. console torn down by close_write) → EOF
            return None
        try:
            data = await state["stream"].receive()
        except Exception as exc:  # EOF/disconnect → end-of-stream
            # anyio EOF/closed, or an underlying device that went away mid-read (e.g. a console
            # PTY torn down during teardown) — all mean "no more bytes", not a driver error.
            if type(exc).__name__ in (
                "EndOfStream",
                "ClosedResourceError",
                "BrokenResourceError",
            ) or isinstance(exc, OSError):
                return None
            _raise_mapped(exc)
        return bytes(data)

    async def stream_write(self, handle: int, data: bytes) -> None:
        state = self._channels.get(handle)
        if state is None:  # closed concurrently → drop (the peer is gone)
            return
        try:
            await state["stream"].send(bytes(data))
        except BaseException as exc:  # noqa: BLE001
            _raise_mapped(exc)

    async def stream_close_write(self, handle: int) -> None:
        state = self._channels.get(handle)
        if state is None:
            return
        if state["cm"] is not None:
            # Console / @exportstream: the client signalling end-of-send ends the session
            # (mirrors the gRPC GOAWAY tearing down the host-side forward). Close the driver
            # stream so the host's read side reaches a clean EOF instead of reading a
            # torn-down device.
            await self.stream_close(handle)
            return
        # Resource stream: half-close the send half only — the driver may still be sending
        # (resource read) and its receive end must not be torn down.
        send_stream = getattr(state["stream"], "send_stream", None)
        if send_stream is not None:
            try:
                await send_stream.aclose()
            except BaseException:  # noqa: BLE001
                pass

    async def stream_close(self, handle: int) -> None:
        state = self._channels.pop(handle, None)
        if not state:
            return
        try:
            if state["cm"] is not None:
                await state["cm"].__aexit__(None, None, None)
            else:
                await state["stream"].aclose()
        except BaseException:  # noqa: BLE001
            pass


def _instantiate_spec(node):
    """Instantiate one driver-tree node from a ``jumpstarter_core.DriverSpecNode`` (the Rust
    core parsed the YAML; this only imports the driver classes by dotted path and constructs
    them). Mirrors the Python ``ExporterConfigV1Alpha1DriverInstance.instantiate()`` cases:
    a ``reference`` → Proxy, a ``type`` → the driver class, otherwise a Composite group."""
    from jumpstarter.common.importlib import import_class

    if node.reference:
        from jumpstarter_driver_composite.driver import Proxy

        return Proxy(ref=node.reference)

    children = {name: _instantiate_spec(child) for name, child in node.children.items()}

    if node.type:
        driver_class = import_class(node.type, [], True)
        return driver_class(
            description=node.description or None,
            methods_description=dict(node.methods_description),
            children=children,
            **json.loads(node.config_json),
        )

    from jumpstarter_driver_composite.driver import Composite

    return Composite(children=children)


class DriverHostFactory:
    """Builds a fresh :class:`DriverHost` per lease. The Rust core parses the exporter config
    YAML (``jc.load_exporter_spec``); Python only instantiates the driver tree (importing
    driver classes by dotted path). ``new_host`` is sync."""

    def __init__(self, config_path: str):
        self._config_path = config_path

    def new_host(self) -> DriverHost:
        from jumpstarter.common.importlib import import_class

        spec = jc.load_exporter_spec(self._config_path)
        children = {name: _instantiate_spec(node) for name, node in spec.export.items()}
        composite = import_class("jumpstarter_driver_composite.driver.Composite", [], True)
        root = composite(description=spec.description or None, methods_description={}, children=children)
        return DriverHost(root)


# --------------------------------------------------------------------------------------
# Local in-process bridge: present a DriverHost as the ClientSession interface (no network)
# --------------------------------------------------------------------------------------


class _LocalResultStream:
    def __init__(self, host: DriverHost, handle: int):
        self._host = host
        self._handle = handle

    async def next(self):
        item = await self._host.streaming_next(self._handle)
        if item is None:
            await self._host.streaming_close(self._handle)
        return item


class _LocalByteStream:
    def __init__(self, host: DriverHost, opened):
        self._host = host
        self._handle = opened.handle
        self._meta = {e.key: e.value for e in opened.initial_metadata}

    def initial_metadata(self) -> str:
        return json.dumps(self._meta)

    async def read(self):
        return await self._host.stream_read(self._handle)

    async def write(self, data) -> None:
        await self._host.stream_write(self._handle, bytes(data))

    async def close_write(self) -> None:
        await self._host.stream_close_write(self._handle)

    async def close(self) -> None:
        await self._host.stream_close(self._handle)


class LocalSession:
    """Presents the ``jumpstarter_core.ClientSession`` interface over a local
    :class:`DriverHost` — used by ``serve()`` to run drivers in-process without a network."""

    def __init__(self, host: DriverHost):
        self._host = host

    async def get_report(self) -> str:
        nodes = await self._host.describe()
        return json.dumps(
            [
                {
                    "uuid": n.uuid,
                    "parent_uuid": n.parent_uuid,
                    "labels": dict(n.labels),
                    "description": n.description,
                    "methods_description": dict(n.methods_description),
                }
                for n in nodes
            ]
        )

    async def driver_call(self, uuid: str, method_name: str, args_json: str) -> str:
        return await self._host.driver_call(uuid, method_name, args_json)

    async def streaming_driver_call(self, uuid: str, method_name: str, args_json: str):
        handle = await self._host.streaming_open(uuid, method_name, args_json)
        return _LocalResultStream(self._host, handle)

    async def stream(self, request_json: str):
        opened = await self._host.open_stream(request_json)
        return _LocalByteStream(self._host, opened)

    async def end_session(self) -> bool:
        return True

    async def get_status(self) -> str:
        # No lease/hook lifecycle locally; the client treats Unimplemented as "ready".
        raise jc.DriverError.Unimplemented("get_status not supported in local serve()")

    async def log_stream(self):
        raise jc.DriverError.Unimplemented("log_stream not supported in local serve()")
