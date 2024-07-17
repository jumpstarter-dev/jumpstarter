from jumpstarter.exporter import Session, Registration
from jumpstarter.drivers.power import MockPower
from jumpstarter.client import Lease, Client
from jumpstarter.common import MetadataFilter
from jumpstarter.v1 import jumpstarter_pb2_grpc
import itertools
import socket
import pytest
import anyio
import grpc

pytestmark = pytest.mark.anyio


@pytest.mark.skipif(
    socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect_ex(("localhost", 8083))
    != 0,
    reason="controller not available",
)
async def test_listener():
    e = Session(
        labels={"jumpstarter.dev/name": "exporter"},
        device_factory=lambda: MockPower(labels={"jumpstarter.dev/name": "power"}),
    )

    credentials = grpc.composite_channel_credentials(
        grpc.local_channel_credentials(),
        grpc.access_token_call_credentials(str(e.uuid)),
    )

    channel = grpc.aio.secure_channel("localhost:8083", credentials)
    controller = jumpstarter_pb2_grpc.ControllerServiceStub(channel)

    async with Registration(
        controller=controller,
        uuid=e.uuid,
        labels={"jumpstarter.dev/name": "exporter"},
        device_reports=e.root_device.reports(),
    ) as r:
        async with anyio.create_task_group() as tg:
            tg.start_soon(r.serve, e)

            async with Lease(
                controller=controller,
                metadata_filter=MetadataFilter(
                    labels={
                        "jumpstarter.dev/name": "exporter",
                    }
                ),
            ) as lease:
                async with lease.connect() as inner:
                    client = Client(inner)
                    await client.sync()
                    assert await client.power.on() == "ok"
                    assert await client.power.off() == "ok"

            tg.cancel_scope.cancel()
