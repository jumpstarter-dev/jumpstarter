"""Integration tests for native gRPC power driver services.

Verifies that MockPower is served as a native gRPC service alongside
the legacy ExporterService, and that both paths work correctly.
"""

import grpc
import pytest
from google.protobuf import empty_pb2

# gRPC async server requires asyncio (not trio)
pytestmark = pytest.mark.anyio


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    """Override anyio backend to asyncio-only for gRPC tests."""
    return request.param


from jumpstarter_driver_power.common import PowerReading
from jumpstarter_driver_power.driver import MockPower, SyncMockPower
from jumpstarter_driver_power.power.v1 import power_pb2_grpc
from jumpstarter_driver_power.servicer import SERVICE_NAME as POWER_SERVICE_NAME

from jumpstarter.common.utils import serve
from jumpstarter.exporter.session import Session

# ── Helper ──────────────────────────────────────────────────────────


def _make_session(driver):
    """Create a Session for a driver with native services registered."""
    return Session(
        uuid=driver.uuid,
        labels=driver.labels,
        root_device=driver,
    )


# ── Native gRPC direct call tests ──────────────────────────────────


@pytest.mark.anyio
async def test_native_grpc_on_off():
    """Call On and Off through the native PowerInterface gRPC service."""
    driver = MockPower()
    session = _make_session(driver)
    with session:
        async with session.serve_tcp_async("127.0.0.1", 0) as port:
            async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
                stub = power_pb2_grpc.PowerInterfaceStub(channel)
                # On and Off should succeed without error
                await stub.On(empty_pb2.Empty())
                await stub.Off(empty_pb2.Empty())


@pytest.mark.anyio
async def test_native_grpc_read_stream():
    """Call Read through native gRPC and verify streamed power readings."""
    driver = MockPower()
    session = _make_session(driver)
    with session:
        async with session.serve_tcp_async("127.0.0.1", 0) as port:
            async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
                stub = power_pb2_grpc.PowerInterfaceStub(channel)
                readings = []
                async for r in stub.Read(empty_pb2.Empty()):
                    readings.append(PowerReading(voltage=r.voltage, current=r.current))

                assert len(readings) == 2
                assert readings[0] == PowerReading(voltage=0.0, current=0.0)
                assert readings[1] == PowerReading(voltage=5.0, current=2.0)


@pytest.mark.anyio
async def test_native_grpc_sync_driver():
    """Native gRPC works with synchronous (non-async) driver methods."""
    driver = SyncMockPower()
    session = _make_session(driver)
    with session:
        async with session.serve_tcp_async("127.0.0.1", 0) as port:
            async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
                stub = power_pb2_grpc.PowerInterfaceStub(channel)
                await stub.On(empty_pb2.Empty())
                await stub.Off(empty_pb2.Empty())

                readings = []
                async for r in stub.Read(empty_pb2.Empty()):
                    readings.append(PowerReading(voltage=r.voltage, current=r.current))
                assert len(readings) == 2


# ── Legacy DriverCall backward compatibility ────────────────────────


def test_legacy_driver_call_still_works():
    """Legacy DriverClient.call() path works alongside native services."""
    with serve(MockPower()) as client:
        client.on()
        client.off()
        readings = list(client.read())
        assert len(readings) == 2
        assert readings[0] == PowerReading(voltage=0.0, current=0.0)
        assert readings[1] == PowerReading(voltage=5.0, current=2.0)


# ── Dual-stack: legacy ExporterService alongside native ─────────────


@pytest.mark.anyio
async def test_dual_stack_exporter_and_native():
    """ExporterService.GetReport works on the same server as native services."""
    from jumpstarter_protocol import jumpstarter_pb2_grpc

    driver = MockPower()
    session = _make_session(driver)
    with session:
        async with session.serve_tcp_async("127.0.0.1", 0) as port:
            async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
                # Legacy ExporterService still works
                exporter_stub = jumpstarter_pb2_grpc.ExporterServiceStub(channel)
                report = await exporter_stub.GetReport(empty_pb2.Empty())
                assert report.uuid == str(driver.uuid)
                assert len(report.reports) >= 1

                # Native PowerInterface also works on the same channel
                power_stub = power_pb2_grpc.PowerInterfaceStub(channel)
                await power_stub.On(empty_pb2.Empty())
                await power_stub.Off(empty_pb2.Empty())


# ── gRPC Reflection ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_grpc_reflection_lists_native_services():
    """gRPC server reflection lists the native PowerInterface service."""
    try:
        from grpc_reflection.v1alpha import reflection_pb2, reflection_pb2_grpc
    except ImportError:
        pytest.skip("grpcio-reflection not installed")

    driver = MockPower()
    session = _make_session(driver)
    with session:
        async with session.serve_tcp_async("127.0.0.1", 0) as port:
            async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
                stub = reflection_pb2_grpc.ServerReflectionStub(channel)

                # List all services
                request = reflection_pb2.ServerReflectionRequest(list_services="")
                responses = stub.ServerReflectionInfo(iter([request]))
                service_names = set()
                async for resp in responses:
                    for svc in resp.list_services_response.service:
                        service_names.add(svc.name)

                # The native power service should be listed
                assert POWER_SERVICE_NAME in service_names, (
                    f"Expected {POWER_SERVICE_NAME} in reflection services, got: {service_names}"
                )

                # ExporterService should also still be listed
                assert "jumpstarter.v1.ExporterService" in service_names


# ── UUID routing via native gRPC metadata ───────────────────────────


@pytest.mark.anyio
async def test_native_grpc_uuid_metadata_routing():
    """UUID metadata routes to the correct driver when using native gRPC."""
    driver = MockPower()
    session = _make_session(driver)
    with session:
        async with session.serve_tcp_async("127.0.0.1", 0) as port:
            async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
                stub = power_pb2_grpc.PowerInterfaceStub(channel)
                # Explicitly provide UUID metadata (should work for single instance)
                metadata = (("x-jumpstarter-driver-uuid", str(driver.uuid)),)
                await stub.On(empty_pb2.Empty(), metadata=metadata)
                await stub.Off(empty_pb2.Empty(), metadata=metadata)


@pytest.mark.anyio
async def test_native_grpc_wrong_uuid_returns_not_found():
    """Providing a non-existent UUID in metadata returns NOT_FOUND."""
    driver = MockPower()
    session = _make_session(driver)
    with session:
        async with session.serve_tcp_async("127.0.0.1", 0) as port:
            async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
                stub = power_pb2_grpc.PowerInterfaceStub(channel)
                metadata = (("x-jumpstarter-driver-uuid", "nonexistent-uuid"),)
                with pytest.raises(grpc.aio.AioRpcError) as exc_info:
                    await stub.On(empty_pb2.Empty(), metadata=metadata)
                assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND
