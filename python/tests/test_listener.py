import socket
from uuid import uuid4

import anyio
import grpc
import pytest
from anyio.to_thread import run_sync

from jumpstarter.client import LeaseRequest, client_from_channel
from jumpstarter.common import MetadataFilter
from jumpstarter.drivers.power import MockPower
from jumpstarter.exporter import Exporter
from jumpstarter.v1 import jumpstarter_pb2_grpc

pytestmark = pytest.mark.anyio


@pytest.mark.skipif(
    socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect_ex(("localhost", 8083)) != 0,
    reason="controller not available",
)
async def test_listener():
    uuid = uuid4()

    credentials = grpc.composite_channel_credentials(
        grpc.local_channel_credentials(),
        grpc.access_token_call_credentials(str(uuid)),
    )

    channel = grpc.aio.secure_channel("localhost:8083", credentials)
    controller = jumpstarter_pb2_grpc.ControllerServiceStub(channel)

    async with Exporter(
        controller=controller,
        uuid=uuid,
        name="exporter",
        device_factory=lambda: MockPower(name="power"),
    ) as r:
        async with anyio.create_task_group() as tg:
            tg.start_soon(r.serve)

            async with LeaseRequest(
                controller=controller,
                metadata_filter=MetadataFilter(name="exporter"),
            ) as lease:
                async with anyio.from_thread.BlockingPortal() as portal:
                    async with lease.connect() as inner:
                        client = await client_from_channel(inner, portal)
                        assert await run_sync(client.on) == "ok"

                    async with lease.connect() as inner:
                        client = await client_from_channel(inner, portal)
                        assert await run_sync(client.on) == "ok"

            tg.cancel_scope.cancel()
