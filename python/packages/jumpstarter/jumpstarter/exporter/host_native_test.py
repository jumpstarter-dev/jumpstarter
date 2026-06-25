"""Live native-path test: descriptors flow Python host → Rust core → real driver.

Proves the *missing link* this change adds: the exporter host's ``describe()`` now ships each
driver a self-contained ``FileDescriptorSet`` (the interface file plus its transitive well-known-
type dependency files, e.g. ``google/protobuf/empty.proto``), the Rust core decodes the set, merges
deps-first, builds one descriptor pool, and serves the per-driver interface natively. A native
``PowerInterface/On`` unary call must reach a real ``MockPower`` driver.

The harness stands up the *real* native gRPC seam (``serve_driver_host`` over a UDS — the same
ExporterService/RouterService + native demux the live exporter serves) and issues a native unary via
``jumpstarter_core.ClientSession`` (the same client the Python driver clients use). No mocks on
either side: the descriptor set is built by the real ``descriptor_builder`` and the call lands in
``MockPower.on()``.
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

# These all require the freshly-built native wheel (make sync-native).
from jumpstarter_core import ClientSession, serve_driver_host
from jumpstarter_core.jumpstarter_core import uniffi_set_event_loop

from jumpstarter.exporter.host import DriverHost, DriverHostFactory

_POWER_ON_PATH = "/jumpstarter.interfaces.power.v1.PowerInterface/On"

_CONFIG_YAML = """\
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: native-test
export:
  power:
    type: jumpstarter_driver_power.driver.MockPower
"""


async def _power_uuid() -> str:
    """The MockPower instance uuid (the demux target) via the same describe() the host serves."""
    from jumpstarter_driver_power.driver import MockPower

    host = DriverHost(MockPower(), root_name="power")
    nodes = await host.describe()
    # Single driver → the only node. Its descriptor_set must be populated (the native surface).
    (node,) = nodes
    assert node.descriptor_set is not None, "MockPower must ship a native descriptor set"
    return node.uuid


@pytest.mark.anyio
async def test_native_power_on_reaches_real_mockpower():
    """A native ``PowerInterface/On`` call routes through the Rust demux + dynamic dispatch into a
    real ``MockPower.on()``, proving descriptors flowed from the Python host to the Rust core."""
    uniffi_set_event_loop(asyncio.get_running_loop())

    with tempfile.TemporaryDirectory() as tmp:
        uds = str(Path(tmp) / "host.sock")
        factory = DriverHostFactory.from_yaml(_CONFIG_YAML)

        # Serve the driver host (native seam) for the duration of the test.
        server = asyncio.create_task(serve_driver_host(uds, factory))
        try:
            # Wait for the socket to appear (the server binds before serving).
            for _ in range(200):
                if Path(uds).exists():
                    break
                await asyncio.sleep(0.025)
            assert Path(uds).exists(), "serve_driver_host never bound the socket"

            session = await ClientSession.connect(uds)

            # The uuid the served host assigns to its MockPower instance (from get_report).
            import json

            reports = json.loads(await session.get_report())
            assert len(reports) == 1, reports
            uuid = reports[0]["uuid"]

            # NATIVE unary On: Empty request (empty body) → Empty response (empty body). The
            # x-jumpstarter-driver-uuid header is injected by native_unary from `uuid`.
            resp = await session.native_unary(uuid, _POWER_ON_PATH, b"")
            assert bytes(resp) == b"", "PowerInterface/On returns Empty (empty bytes)"

            await session.end_session()
        finally:
            server.cancel()
            try:
                await server
            except (asyncio.CancelledError, Exception):
                pass


@pytest.mark.anyio
async def test_driver_call_on_routes_through_native_path():
    """``ClientSession.driver_call`` (the unchanged Python-facing API, decision #10) now bridges to
    the native path inside the Rust core: it resolves the driver's interface descriptor from
    ``GetReport``, encodes the positional args into the native request message, dispatches via the
    demux, and decodes the response — landing in a real ``MockPower.on()``. The Python ``call("on")``
    surface is identical; only the Rust transport changed."""
    uniffi_set_event_loop(asyncio.get_running_loop())

    with tempfile.TemporaryDirectory() as tmp:
        uds = str(Path(tmp) / "host.sock")
        factory = DriverHostFactory.from_yaml(_CONFIG_YAML)

        server = asyncio.create_task(serve_driver_host(uds, factory))
        try:
            for _ in range(200):
                if Path(uds).exists():
                    break
                await asyncio.sleep(0.025)
            assert Path(uds).exists(), "serve_driver_host never bound the socket"

            session = await ClientSession.connect(uds)

            import json

            reports = json.loads(await session.get_report())
            uuid = reports[0]["uuid"]

            # The UNARY driver_call surface the Python PowerClient.on() uses: args is a JSON array,
            # result is JSON. on() returns None → "null". This now travels native end-to-end.
            result = await session.driver_call(uuid, "on", "[]")
            assert json.loads(result) is None, "on() returns None"

            # off() too (a second native unary, proving the cached dispatch table is reused).
            result_off = await session.driver_call(uuid, "off", "[]")
            assert json.loads(result_off) is None

            await session.end_session()
        finally:
            server.cancel()
            try:
                await server
            except (asyncio.CancelledError, Exception):
                pass


@pytest.mark.anyio
async def test_streaming_driver_call_read_routes_through_native_path():
    """``ClientSession.streaming_driver_call`` (the Python ``PowerClient.read()`` surface) now bridges
    to the native **server-streaming** path: the descriptor's ``Read`` method is server-streaming, so
    the request is encoded, the native stream opened through the demux + dynamic dispatch, and each
    yielded ``PowerReading`` message decoded back to JSON — landing in a real ``MockPower.read()``
    async generator. The Python ``read()`` surface is identical; only the Rust transport changed."""
    uniffi_set_event_loop(asyncio.get_running_loop())

    with tempfile.TemporaryDirectory() as tmp:
        uds = str(Path(tmp) / "host.sock")
        factory = DriverHostFactory.from_yaml(_CONFIG_YAML)

        server = asyncio.create_task(serve_driver_host(uds, factory))
        try:
            for _ in range(200):
                if Path(uds).exists():
                    break
                await asyncio.sleep(0.025)
            assert Path(uds).exists(), "serve_driver_host never bound the socket"

            session = await ClientSession.connect(uds)

            import json

            reports = json.loads(await session.get_report())
            uuid = reports[0]["uuid"]

            # The server-streaming read() surface: MockPower.read() yields two PowerReadings. Each
            # is decoded from its native message back to JSON {voltage, current}.
            stream = await session.streaming_driver_call(uuid, "read", "[]")
            readings = []
            while True:
                item = await stream.next()
                if item is None:
                    break
                readings.append(json.loads(item))

            assert readings == [
                {"voltage": 0.0, "current": 0.0},
                {"voltage": 5.0, "current": 2.0},
            ], readings

            await session.end_session()
        finally:
            server.cancel()
            try:
                await server
            except (asyncio.CancelledError, Exception):
                pass


@pytest.mark.anyio
async def test_describe_ships_decodable_self_contained_descriptor_set():
    """``describe()`` yields a decodable, deps-first ``FileDescriptorSet`` for the power driver:
    the interface file plus its ``google/protobuf/empty.proto`` dependency."""
    from google.protobuf import descriptor_pb2

    uuid = await _power_uuid()
    assert uuid

    from jumpstarter_driver_power.driver import MockPower

    host = DriverHost(MockPower())
    (node,) = await host.describe()
    fset = descriptor_pb2.FileDescriptorSet()
    fset.ParseFromString(bytes(node.descriptor_set))

    names = [f.name for f in fset.file]
    assert "google/protobuf/empty.proto" in names, names
    assert "power.proto" in names, names
    # deps-first: the dependency precedes the file that imports it.
    assert names.index("google/protobuf/empty.proto") < names.index("power.proto"), names

    services = [(s.name, sorted(m.name for m in s.method)) for f in fset.file for s in f.service]
    assert services == [("PowerInterface", ["Off", "On", "Read"])], services
