import socket
from uuid import uuid4

import anyio
import grpc
import pytest
from anyio.from_thread import start_blocking_portal
from anyio.to_process import run_sync

from jumpstarter.client import LeaseRequest
from jumpstarter.common import MetadataFilter
from jumpstarter.common.grpc import secure_channel
from jumpstarter.drivers.power.driver import MockPower
from jumpstarter.exporter import Exporter
from jumpstarter.v1 import jumpstarter_pb2_grpc

pytestmark = pytest.mark.anyio


def blocking(uuid):
    credentials = grpc.composite_channel_credentials(
        grpc.local_channel_credentials(),
        grpc.access_token_call_credentials(str(uuid)),
    )

    with start_blocking_portal() as portal:
        with LeaseRequest(
            channel=portal.call(secure_channel, "localhost:8083", credentials),
            metadata_filter=MetadataFilter(name="exporter"),
            portal=portal,
        ) as lease:
            with lease.connect() as client:
                assert client.on() == "ok"

            with lease.connect() as client:
                assert client.on() == "ok"


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

            await run_sync(blocking, uuid)

            tg.cancel_scope.cancel()
