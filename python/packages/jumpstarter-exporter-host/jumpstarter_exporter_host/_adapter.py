"""Thin in-process driver host adapter.

Implements the ``jumpstarter_core`` ``DriverHostProtocol``/``DriverHostFactoryProtocol``
by reusing the existing ``Driver`` classes + ``@export``/``@exportstream`` markers, but
crossing the FFI boundary as **plain JSON** (the Rust core owns the proto-``Value`` codec,
the report assembly, the gRPC, and the router framing). This is the replacement for the
``slim_driver_host.py`` subprocess: no second process, no gRPC, no protobuf, no pydantic
in the dispatch path.

Streams are handle-based (a UniFFI constraint): ``streaming_open``/``open_stream`` register
the iterator/byte-channel under an integer handle that Rust drives by. All methods run on
the single Python event loop (UniFFI schedules the foreign coroutines there), so the handle
registries need no locking.
"""

from __future__ import annotations

import json
import logging
import math
from inspect import isasyncgenfunction, iscoroutinefunction
from itertools import count
from pathlib import Path
from typing import Any

from anyio import to_thread

import jumpstarter_core as jc

_log = logging.getLogger("jumpstarter_exporter_host")
from jumpstarter.driver.decorators import (
    MARKER_DRIVERCALL,
    MARKER_MAGIC,
    MARKER_STREAMCALL,
    MARKER_STREAMING_DRIVERCALL,
)


def _to_jsonable(value: Any) -> Any:
    """Normalize a driver return value to a JSON-able form matching the legacy
    ``pydantic TypeAdapter(Any).dump_python(mode="json")`` quirks the Rust codec expects:
    non-finite floats → ``None``, ``bytes`` → UTF-8 string, tuples/sets → lists, pydantic
    models → ``.model_dump(mode="json")``. (Verified against the Rust value golden corpus.)
    """
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8")
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):  # pydantic BaseModel (the driver brings pydantic)
        return _to_jsonable(model_dump(mode="json"))
    raise TypeError(f"driver returned a non-JSON-able value of type {type(value)!r}")


def _lookup(driver, name: str, marker: str):
    """Resolve an ``@export``/``@exportstream`` method by name, validating its marker
    (mirrors ``Driver.__lookup_drivercall``)."""
    method = getattr(driver, name, None)
    if method is None or getattr(method, marker, None) != MARKER_MAGIC:
        raise jc.DriverError.NotFound(f"method {name} not found on driver")
    return method


def _raise_mapped(exc: BaseException) -> None:
    """Map a driver exception to the typed ``DriverError`` Rust expects (UniFFI panics on
    any undeclared exception, so this catch must be total)."""
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

    # -- introspection ------------------------------------------------------
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

    # -- unary driver call --------------------------------------------------
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

    # -- streaming driver call (handle-based) -------------------------------
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

    # -- byte streams (handle-based) ----------------------------------------
    async def open_stream(self, request_json: str) -> jc.OpenStream:
        req = json.loads(request_json)
        uuid = req["uuid"]
        driver = self._driver(uuid)
        if "method" in req:
            # Driver @exportstream (console / serial / network): enter the context manager
            # and expose its anyio byte stream.
            method = _lookup(driver, req["method"], MARKER_STREAMCALL)
            try:
                cm = method()
                stream = await cm.__aenter__()
            except BaseException as exc:  # noqa: BLE001
                _raise_mapped(exc)
            handle = next(self._handles)
            self._channels[handle] = {"cm": cm, "stream": stream}
            return jc.OpenStream(handle=handle, initial_metadata=[])
        # Resource streams (flash/dump) are not yet supported in-process.
        raise jc.DriverError.Unimplemented("resource streams not yet supported in-process")

    async def stream_read(self, handle: int):
        stream = self._channels[handle]["stream"]
        try:
            data = await stream.receive()
        except Exception as exc:  # anyio.EndOfStream / ClosedResourceError → EOF
            if type(exc).__name__ in ("EndOfStream", "ClosedResourceError", "BrokenResourceError"):
                return None
            _raise_mapped(exc)
        return bytes(data)

    async def stream_write(self, handle: int, data: bytes) -> None:
        try:
            await self._channels[handle]["stream"].send(bytes(data))
        except BaseException as exc:  # noqa: BLE001
            _raise_mapped(exc)

    async def stream_close_write(self, handle: int) -> None:
        stream = self._channels[handle]["stream"]
        send_eof = getattr(stream, "send_eof", None)
        if send_eof:
            try:
                await send_eof()
            except BaseException:  # noqa: BLE001
                pass

    async def stream_close(self, handle: int) -> None:
        state = self._channels.pop(handle, None)
        if state:
            try:
                await state["cm"].__aexit__(None, None, None)
            except BaseException:  # noqa: BLE001
                pass


class DriverHostFactory:
    """Builds a fresh :class:`DriverHost` per lease from the exporter config (the same
    Composite-root ``instantiate()`` the slim host used). ``new_host`` is sync."""

    def __init__(self, config_path: str):
        self._config_path = config_path

    def new_host(self) -> DriverHost:
        # Imported lazily so the heavy config/driver imports happen at provision time.
        from jumpstarter.config.exporter import (
            ExporterConfigV1Alpha1,
            ExporterConfigV1Alpha1DriverInstance,
        )

        config = ExporterConfigV1Alpha1.load_path(Path(self._config_path))
        root = ExporterConfigV1Alpha1DriverInstance(
            type="jumpstarter_driver_composite.driver.Composite",
            description=config.description,
            children=config.export,
        ).instantiate()
        return DriverHost(root)
