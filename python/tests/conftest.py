from jumpstarter.exporter import Exporter, ExporterSession
from jumpstarter.client import Client
import pytest
import grpc


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
async def setup_exporter(request):
    server = grpc.aio.server()

    try:
        s = ExporterSession(devices_factory=request.param)
    except FileNotFoundError:
        pytest.skip("fail to find required devices")

    e = Exporter(labels={"jumpstarter.dev/name": "exporter"}, session=s)
    e.add_to_server(server)

    server.add_insecure_port("localhost:50051")
    await server.start()

    client = Client(grpc.aio.insecure_channel("localhost:50051"))
    await client.sync()
    yield client

    await server.stop(grace=None)
    await server.wait_for_termination()
