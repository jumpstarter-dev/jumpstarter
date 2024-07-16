from jumpstarter.exporter import Exporter, ExporterSession, Listener
from jumpstarter.drivers.power import MockPower
import pytest
import grpc

pytestmark = pytest.mark.anyio


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
        grpc.access_token_call_credentials("test-exporter"),
    )

    listener = Listener(grpc.aio.secure_channel("localhost:8083", credentials))
    await listener.serve(e)

    await server.stop(grace=None)
    await server.wait_for_termination()
