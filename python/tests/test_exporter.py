import os
import tempfile

import grpc
import pytest

from jumpstarter.client import client_from_channel
from jumpstarter.drivers.composite import Composite
from jumpstarter.drivers.power import MockPower, PowerReading
from jumpstarter.drivers.storage import MockStorageMux
from jumpstarter.exporter import Session

pytestmark = pytest.mark.anyio


@pytest.fixture
async def setup_client(request, anyio_backend):
    server = grpc.aio.server()

    try:
        e = Session(labels={"jumpstarter.dev/name": "exporter"}, root_device=request.param)
    except FileNotFoundError:
        pytest.skip("fail to find required devices")

    e.add_to_server(server)

    server.add_insecure_port("localhost:50051")
    await server.start()

    client = await client_from_channel(grpc.aio.insecure_channel("localhost:50051"))
    yield client

    await server.stop(grace=None)
    await server.wait_for_termination()


@pytest.mark.parametrize(
    "setup_client",
    [
        Composite(
            labels={"jumpstarter.dev/name": "composite0"},
            children=[
                MockPower(labels={"jumpstarter.dev/name": "power0"}),
                MockStorageMux(labels={"jumpstarter.dev/name": "storage"}),
                Composite(
                    labels={"jumpstarter.dev/name": "composite1"},
                    children=[
                        MockPower(labels={"jumpstarter.dev/name": "power1"}),
                    ],
                ),
            ],
        )
    ],
    indirect=True,
)
async def test_exporter_mock(setup_client):
    client = setup_client

    assert await client.composite1.power1.on() == "ok"
    assert [reading async for reading in client.composite1.power1.read()] == [
        PowerReading(voltage=0.0, current=0.0),
        PowerReading(voltage=5.0, current=2.0),
    ]

    with tempfile.NamedTemporaryFile(delete=False) as tempf:
        tempf.write(b"thisisatestfile")
        tempf.close()

        async with client.storage.local_file(tempf.name) as file:
            await client.storage.write(file)

        os.unlink(tempf.name)
