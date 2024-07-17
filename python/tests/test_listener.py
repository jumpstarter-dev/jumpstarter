from jumpstarter.exporter import Exporter, ExporterSession, Listener
from jumpstarter.drivers.power import MockPower
from jumpstarter.client import Lease, Client
from jumpstarter.common import MetadataFilter
from jumpstarter.v1 import jumpstarter_pb2_grpc
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
    server = grpc.aio.server()

    s = ExporterSession(
        devices_factory=lambda session: [
            MockPower(session=session, labels={"jumpstarter.dev/name": "power"}),
        ]
    )

    e = Exporter(labels={"jumpstarter.dev/name": "exporter"}, session=s)
    e.add_to_server(server)

    server.add_insecure_port("localhost:50051")
    await server.start()

    credentials = grpc.composite_channel_credentials(
        grpc.local_channel_credentials(),
        grpc.access_token_call_credentials(str(e.uuid)),
    )

    channel = grpc.aio.secure_channel("localhost:8083", credentials)
    controller = jumpstarter_pb2_grpc.ControllerServiceStub(channel)

    listener = Listener(channel)

    async with anyio.create_task_group() as tg:
        tg.start_soon(listener.serve, e)
        await anyio.sleep(1)

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

        tg.cancel_scope.cancel()

    await server.stop(grace=None)
    await server.wait_for_termination()
