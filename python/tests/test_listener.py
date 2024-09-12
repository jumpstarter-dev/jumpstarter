# These tests are flaky
# https://github.com/grpc/grpc/issues/25364

from contextlib import asynccontextmanager
from uuid import uuid4

import grpc
import pytest
from anyio import create_task_group
from anyio.from_thread import start_blocking_portal

from jumpstarter.client import LeaseRequest
from jumpstarter.client.lease import Lease
from jumpstarter.common import MetadataFilter
from jumpstarter.common.streams import connect_router_stream
from jumpstarter.drivers.power.driver import MockPower
from jumpstarter.exporter import Exporter

pytestmark = pytest.mark.anyio


@pytest.mark.xfail(raises=RuntimeError)
async def test_router(mock_controller, monkeypatch):
    uuid = uuid4()

    exporter = Exporter(
        channel=grpc.aio.insecure_channel("grpc.invalid"),
        uuid=uuid,
        labels={},
        device_factory=lambda: MockPower(),
    )

    @asynccontextmanager
    async def handle_async(stream):
        async with connect_router_stream(mock_controller, str(uuid), stream):
            yield

    async with exporter._Exporter__handle(mock_controller, str(uuid)):
        with start_blocking_portal() as portal:
            lease = Lease(channel=grpc.aio.insecure_channel("grpc.invalid"), uuid=uuid, portal=portal)

            monkeypatch.setattr(lease, "handle_async", handle_async)

            async with lease.connect_async() as client:
                assert await client.call_async("on") == "ok"


@pytest.mark.xfail(raises=RuntimeError)
async def test_controller(mock_controller):
    uuid = uuid4()

    async with Exporter(
        channel=grpc.aio.insecure_channel(mock_controller),
        uuid=uuid,
        labels={},
        device_factory=lambda: MockPower(),
    ) as exporter:
        async with create_task_group() as tg:
            tg.start_soon(exporter.serve)

            with start_blocking_portal() as portal:
                async with LeaseRequest(
                    channel=grpc.aio.insecure_channel(mock_controller),
                    metadata_filter=MetadataFilter(),
                    portal=portal,
                ) as lease:
                    async with lease.connect_async() as client:
                        assert await client.call_async("on") == "ok"

                    async with lease.connect_async() as client:
                        assert await client.call_async("on") == "ok"

            tg.cancel_scope.cancel()
