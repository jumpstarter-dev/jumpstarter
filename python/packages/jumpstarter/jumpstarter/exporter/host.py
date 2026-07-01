"""In-process driver host adapter + a local in-process client bridge.

``DriverHost`` reuses the existing ``Driver`` classes + ``@export``/``@exportstream``
markers and crosses the boundary as plain JSON (the Rust core owns the proto-``Value``
codec, report assembly, gRPC and router framing). It implements the ``jumpstarter_core``
``DriverHost``/``DriverHostFactory`` protocols for the in-process exporter.

``LocalSession`` bridges that same ``DriverHost`` to the ``jumpstarter_core.ClientSession``
interface *without* a network/transport, so ``serve()`` runs driver tests through the same
FFI-shaped dispatch (no old Python exporter, no grpc). The ``jumpstarter_core.*`` types here are plain
data carriers — the local path makes no FFI runtime calls.

Streams are handle-based: ``streaming_open``/``open_stream`` register the iterator/byte
channel under an integer handle the caller drives by.
"""

from __future__ import annotations

import json
import logging
import os
from inspect import isasyncgenfunction, iscoroutinefunction
from itertools import count
from typing import Any
from uuid import uuid4

from anyio import to_thread
from jumpstarter_core import (
    DriverError,
    DriverNode,
    MetadataEntry,
    OpenStream,
    StreamCompressor,
    StreamDecompressor,
    load_exporter_spec,
    load_exporter_spec_str,
)

# The foreign-trait base classes the Rust core dispatches into. UniFFI 0.31 checks the
# Python impl with a nominal ``isinstance`` (0.29 duck-typed), so these must be subclassed.
from jumpstarter_core import DriverHost as _CoreDriverHost
from jumpstarter_core import DriverHostFactory as _CoreDriverHostFactory

from jumpstarter.common.jsonable import to_jsonable as _to_jsonable
from jumpstarter.common.resources import ClientStreamResource
from jumpstarter.driver.decorators import (
    MARKER_DRIVERCALL,
    MARKER_MAGIC,
    MARKER_STREAMCALL,
    MARKER_STREAMING_DRIVERCALL,
)
from jumpstarter.driver.proto_marshal import DriverMarshaller, build_marshaller, decode_request, encode_response
from jumpstarter.streams.common import create_memory_stream

_log = logging.getLogger("jumpstarter.exporter.host")


def _interface_class(driver) -> type | None:
    """Find the driver's INTERFACE class — the base that declares the per-interface contract
    (the abstract ``@export`` methods + the ``client()`` classmethod). A driver subclasses its
    interface alongside ``Driver`` (e.g. ``MockPower(PowerInterface, Driver)``), so the interface
    is the MRO entry that defines ``client`` in its own namespace and is NOT itself a ``Driver``
    subclass (which excludes ``Driver`` and the concrete driver). Returns ``None`` if no such base
    exists (the driver then has no native surface)."""
    from jumpstarter.driver.base import Driver

    for cls in type(driver).__mro__:
        if cls is Driver or cls is object:
            continue
        if "client" in cls.__dict__ and not issubclass(cls, Driver):
            return cls
    return None


def _descriptor_set_bytes(driver) -> bytes | None:
    """Serialize the self-contained ``FileDescriptorSet`` for a driver's native interface (the
    interface's own ``FileDescriptorProto`` plus its transitive well-known-type dependency files,
    deps-first), so the Rust core can build a descriptor pool with no external imports to resolve.

    The descriptor is built from the driver's **interface ABC** when it has one (the stable contract
    name, e.g. ``PowerInterface``), else from the **concrete driver class** itself — introspecting
    its full ``@export`` surface across the MRO. So EVERY driver gets a native descriptor, native is
    the only call path, and there is no legacy fallback. Returns ``None`` (logged, never raises) only
    if the build genuinely fails — describe() must not crash on one bad driver.

    Delegates to ``descriptor_builder.build_file_descriptor_set`` so the marshaller (proto_marshal)
    decodes/encodes against the exact same set this host advertises."""
    iface = _interface_class(driver) or type(driver)
    try:
        from jumpstarter.driver.descriptor_builder import build_file_descriptor_set

        return build_file_descriptor_set(iface).SerializeToString()
    except Exception as exc:  # noqa: BLE001 — describe() must survive a bad/uninspectable interface
        _log.warning(
            "native descriptor build failed for %s (%s); driver has no native surface",
            type(driver).__name__,
            exc,
        )
        return None


def _lookup(driver, name: str, marker: str):
    """Resolve an ``@export``/``@exportstream`` method by name, validating its marker."""
    method = getattr(driver, name, None)
    if method is None or getattr(method, marker, None) != MARKER_MAGIC:
        raise DriverError.NotFound(f"method {name} not found on driver")
    return method


def _native_method(driver, name: str, marker: str):
    """Like ``_lookup`` but raises ``Unimplemented`` (not ``NotFound``) on a miss, so a method the
    native proto seam can't serve DEFERS to the legacy JSON ``driver_call`` path in the Rust core
    rather than surfacing as a hard error."""
    method = getattr(driver, name, None)
    if method is None or getattr(method, marker, None) != MARKER_MAGIC:
        raise DriverError.Unimplemented(f"method {name} not served by the native path")
    return method


def _raise_mapped(exc: BaseException) -> None:
    """Map a driver exception to the typed ``DriverError`` (UniFFI panics on any undeclared
    exception, so this catch must be total)."""
    if isinstance(exc, DriverError):
        raise exc
    if isinstance(exc, NotImplementedError):
        raise DriverError.Unimplemented(str(exc))
    if isinstance(exc, ValueError):
        raise DriverError.InvalidArgument(str(exc))
    if isinstance(exc, TimeoutError):
        raise DriverError.DeadlineExceeded(str(exc))
    raise DriverError.Unknown(str(exc))


class DriverHost(_CoreDriverHost):
    """A lease's driver host: the whole instantiated driver tree, dispatched by UUID."""

    def __init__(self, root, root_name: str | None = None):
        self._root = root
        # The polyglot host serves one top-level `export:` entry directly as the root; its name
        # (the entry key) rides on the root node's `jumpstarter.dev/name` label so the hub can
        # re-parent it. `serve()` builds a root from a local driver and passes no name.
        self._root_name = root_name
        self._by_uuid = {str(uuid): driver for (uuid, _p, _n, driver) in root.enumerate()}
        self._handles = count(1)
        self._result_streams: dict[int, tuple[str, Any]] = {}
        self._channels: dict[int, dict] = {}
        # Native (proto-bytes) dispatch: a per-driver marshaller (None = no native surface → the
        # forward_* seam raises Unimplemented and the Rust core falls back to JSON driver_call), and
        # the server-stream registry (the proto-encoding analog of _result_streams, carrying the spec
        # so each yielded item is encoded against the right response message).
        self._marshallers: dict[str, DriverMarshaller | None] = {}
        self._native_streams: dict[int, tuple[str, Any, Any]] = {}

    def _driver(self, uuid: str):
        driver = self._by_uuid.get(uuid)
        if driver is None:
            raise DriverError.NotFound(f"unknown driver uuid: {uuid}")
        return driver

    def _marshaller(self, uuid: str) -> DriverMarshaller | None:
        """The driver's native dispatch table, built lazily and memoized (``None`` included). Built
        synchronously on the event-loop thread — never inside ``to_thread`` — so there is no
        cross-thread ``DescriptorPool`` race; the driver tree is fixed per lease so uuid keying is
        stable."""
        if uuid not in self._marshallers:
            self._marshallers[uuid] = build_marshaller(self._driver(uuid))
        return self._marshallers[uuid]

    def _native_spec(self, uuid: str, path: str, *, server_streaming: bool):
        """Resolve the ``MethodSpec`` for ``path``, or raise ``Unimplemented`` (→ Rust JSON fallback)
        when the driver has no native surface, the path is unknown, or the path's streaming shape
        doesn't match the seam it arrived on."""
        marshaller = self._marshaller(uuid)
        if marshaller is None:
            raise DriverError.Unimplemented(f"driver {uuid} has no native surface")
        spec = marshaller.methods.get(path)
        if spec is None:
            raise DriverError.Unimplemented(f"no native method for {path}")
        if spec.server_streaming != server_streaming:
            # A unary call must not arrive on the server-stream seam or vice-versa (the Rust core
            # frames unary-as-one-item-stream itself and never routes it here).
            raise DriverError.Unimplemented(f"{path} streaming shape mismatch")
        return spec

    async def describe(self) -> list[DriverNode]:
        _log.debug("describe: introspecting driver tree")
        nodes = []
        for (uuid, parent, name, driver) in self._root.enumerate():
            labels = dict(driver.labels)
            labels.update(driver.extra_labels())
            labels["jumpstarter.dev/client"] = driver.client()
            # Children take their name from the parent's child-key; the root entry takes the
            # configured entry name (so the hub re-parents it under the synthesized root).
            node_name = name or (self._root_name if parent is None else None)
            if node_name:
                labels["jumpstarter.dev/name"] = node_name
            nodes.append(
                DriverNode(
                    uuid=str(uuid),
                    parent_uuid=str(parent.uuid) if parent else None,
                    labels=labels,
                    description=driver.description or None,
                    methods_description=driver.methods_description or {},
                    # Self-contained FileDescriptorSet for the native per-driver gRPC surface (None
                    # if the driver's interface can't be introspected — never crashes describe()).
                    descriptor_set=_descriptor_set_bytes(driver),
                )
            )
        return nodes

    # --- native (proto-bytes) dispatch -------------------------------------------------------
    # forward_unary / forward_server_stream carry REAL proto messages (decoded/encoded by
    # proto_marshal against the driver's own descriptor), so the Rust core forwards opaque bytes
    # instead of the JSON driver_call codec — parity with the JVM GrpcServiceDriverHost. Anything
    # the native path can't serve raises Unimplemented, and the core falls back to driver_call
    # below, keeping every existing @export driver working unchanged.

    async def forward_unary(self, uuid: str, path: str, body: bytes) -> bytes:
        _log.debug("forward_unary uuid=%s path=%s", uuid, path)
        spec = self._native_spec(uuid, path, server_streaming=False)
        method = _native_method(self._driver(uuid), spec.export_name, MARKER_DRIVERCALL)
        try:
            args = decode_request(spec, bytes(body))
            if iscoroutinefunction(method):
                result = await method(*args)
            else:
                result = await to_thread.run_sync(method, *args)
            return encode_response(spec, result)
        except BaseException as exc:  # noqa: BLE001 — must be total (see _raise_mapped)
            _raise_mapped(exc)

    async def forward_server_stream(self, uuid: str, path: str, body: bytes) -> int:
        _log.debug("forward_server_stream uuid=%s path=%s", uuid, path)
        spec = self._native_spec(uuid, path, server_streaming=True)
        method = _native_method(self._driver(uuid), spec.export_name, MARKER_STREAMING_DRIVERCALL)
        try:
            # Decode BEFORE opening so a bad request fails at open (mirrors the Rust core).
            args = decode_request(spec, bytes(body))
            if isasyncgenfunction(method):
                state = ("async", method(*args), spec)
            else:
                state = ("sync", iter(method(*args)), spec)
        except BaseException as exc:  # noqa: BLE001
            _raise_mapped(exc)
        handle = next(self._handles)
        self._native_streams[handle] = state
        return handle

    async def forward_stream_next(self, handle: int) -> bytes | None:
        entry = self._native_streams.get(handle)
        if entry is None:
            return None
        kind, it, spec = entry
        try:
            if kind == "async":
                try:
                    item = await it.__anext__()
                except StopAsyncIteration:
                    return None
            else:
                sentinel = object()

                def _next():
                    return next(it, sentinel)

                item = await to_thread.run_sync(_next)
                if item is sentinel:
                    return None
        except BaseException as exc:  # noqa: BLE001
            _raise_mapped(exc)
        return encode_response(spec, item)

    async def forward_stream_close(self, handle: int) -> None:
        entry = self._native_streams.pop(handle, None)
        if entry and entry[0] == "async":
            aclose = getattr(entry[1], "aclose", None)
            if aclose:
                try:
                    await aclose()
                except BaseException:  # noqa: BLE001
                    pass

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

    async def open_stream(self, request_json: str) -> OpenStream:
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
            return OpenStream(handle=handle, initial_metadata=[])

        # Resource stream (flash/dump): memory pipe + register the far end in the driver's
        # resource map (a later driver call reads it by uuid).
        remote, resource = create_memory_stream()
        resource_uuid = uuid4()
        driver.resources[resource_uuid] = resource
        # The Rust core owns the wire codec and is the source of truth for the
        # `x_jmp_accept_encoding` negotiation (it injects the accept header into the initial
        # metadata when a supported encoding is requested). The host only builds the resource
        # handle; `x_jmp_content_encoding` rides along but is ignored by the driver (Rust already
        # (de)compressed the bytes).
        encoding = req.get("x_jmp_content_encoding")
        resource_handle = ClientStreamResource(
            uuid=resource_uuid, x_jmp_content_encoding=encoding
        ).model_dump_json()
        metadata = [MetadataEntry(key="resource", value=resource_handle)]
        handle = next(self._handles)
        self._channels[handle] = {"cm": None, "stream": remote}
        return OpenStream(handle=handle, initial_metadata=metadata)

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


class DriverHostFactory(_CoreDriverHostFactory):
    """Builds a fresh :class:`DriverHost` per lease. The Rust core parses the exporter config
    YAML (``load_exporter_spec``); Python only instantiates the driver tree (importing
    driver classes by dotted path). ``new_host`` is sync."""

    def __init__(self, config_path: str = None):
        self._config_path = config_path
        self._config_yaml = None

    @classmethod
    def from_yaml(cls, config_yaml: str) -> "DriverHostFactory":
        """Build from in-memory config YAML (the polyglot hub passes per-entry configs over
        stdin, so no temp file is written)."""
        factory = cls.__new__(cls)
        factory._config_path = None
        factory._config_yaml = config_yaml
        return factory

    def new_host(self) -> DriverHost:
        if self._config_yaml is not None:
            spec = load_exporter_spec_str(self._config_yaml)
        else:
            spec = load_exporter_spec(self._config_path)
        # One top-level `export:` entry per host (the polyglot hub spawns one host per entry).
        # Serve that entry's subtree directly as the root — the hub re-parents it under the
        # synthesized Composite root — so no redundant Composite wrapper is instantiated here.
        (name, node), *rest = spec.export.items()
        if rest:  # defensive: the hub never sends more than one entry
            raise ValueError(f"driver host expected a single export entry, got {1 + len(rest)}")
        return DriverHost(_instantiate_spec(node), root_name=name)


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


_KNOWN_CODECS = frozenset({"gzip", "xz", "bz2", "zstd"})


def _resource_codec(request_json: str) -> str | None:
    """The resource wire codec to apply on the in-process byte plane, or ``None`` for a driver
    ``@exportstream`` / an unrecognized encoding / ``JMP_DISABLE_COMPRESSION=1`` — mirroring the
    Rust host seam's ``parse_codec`` (``foreign.rs``)."""
    if os.environ.get("JMP_DISABLE_COMPRESSION") == "1":
        return None
    try:
        enc = json.loads(request_json).get("x_jmp_content_encoding")
    except (ValueError, TypeError, AttributeError):
        return None
    return enc if enc in _KNOWN_CODECS else None


class _LocalByteStream:
    """In-process resource byte channel for ``serve()``. Production decompresses the uplink /
    compresses the downlink in the Rust host seam (``foreign.rs``); this path has no such seam, so
    it drives the SAME Rust codecs over FFI — a compressed resource flashed under ``serve()`` reaches
    the driver as RAW bytes, exactly like production. ``codec=None`` is a transparent passthrough."""

    def __init__(self, host: DriverHost, opened, codec: str | None = None):
        self._host = host
        self._handle = opened.handle
        self._meta = {e.key: e.value for e in opened.initial_metadata}
        # Uplink (client->driver, flash) DECOMPRESSES; downlink (driver->client, dump) COMPRESSES.
        self._dec = StreamDecompressor(codec) if codec else None
        self._comp = StreamCompressor(codec) if codec else None
        self._comp_done = False
        # Whether data actually flowed each way. A flash has an empty downlink and a dump an empty
        # uplink; an unfed codec must NOT be finalized (no spurious footer — a downlink footer on a
        # flash would be written back into the read-only source by the bidirectional forward_stream).
        self._dec_fed = False
        self._comp_fed = False

    def initial_metadata(self) -> str:
        return json.dumps(self._meta)

    async def read(self):
        if self._comp is None:
            return await self._host.stream_read(self._handle)
        if self._comp_done:
            return None
        # Pull raw driver bytes, compressing, until we have output or hit EOF (skip the encoder's
        # buffering-only empty chunks); flush the compressor footer once at EOF.
        while True:
            raw = await self._host.stream_read(self._handle)
            if raw is None:
                self._comp_done = True
                if not self._comp_fed:
                    return None  # empty downlink (flash) → emit nothing
                tail = bytes(self._comp.finish())
                return tail if tail else None
            self._comp_fed = True
            out = bytes(self._comp.compress(bytes(raw)))
            if out:
                return out

    async def write(self, data) -> None:
        if self._dec is None:
            await self._host.stream_write(self._handle, bytes(data))
            return
        self._dec_fed = True
        out = bytes(self._dec.decompress(bytes(data)))
        if out:
            await self._host.stream_write(self._handle, out)

    async def close_write(self) -> None:
        if self._dec is not None and self._dec_fed:
            tail = bytes(self._dec.finish())
            if tail:
                await self._host.stream_write(self._handle, tail)
        await self._host.stream_close_write(self._handle)

    async def shutdown(self) -> None:
        # Mirrors jumpstarter_core.ClientByteStream.shutdown (renamed from close so the FFI binding
        # doesn't collide with Kotlin's AutoCloseable.close); LocalSession stays a drop-in.
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
        return _LocalByteStream(self._host, opened, _resource_codec(request_json))

    async def end_session(self) -> bool:
        return True

    async def get_status(self) -> str:
        # No lease/hook lifecycle locally; the client treats Unimplemented as "ready".
        raise DriverError.Unimplemented("get_status not supported in local serve()")

    async def log_stream(self):
        raise DriverError.Unimplemented("log_stream not supported in local serve()")
