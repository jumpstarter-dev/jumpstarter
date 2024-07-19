from jumpstarter.drivers.composite.base import ClientFromReports
from jumpstarter.drivers.composite import Composite, CompositeClient
from jumpstarter.drivers.power import MockPower, PowerClient
from jumpstarter.v1 import jumpstarter_pb2_grpc
from collections import OrderedDict
import pytest
import grpc

pytestmark = pytest.mark.anyio


async def test_drivers_composite():
    server = grpc.aio.server()
    server.add_insecure_port("localhost:50051")

    mock = Composite(
        labels={"jumpstarter.dev/name": "composite0"},
        childs=[
            MockPower(labels={"jumpstarter.dev/name": "power0"}),
            Composite(
                labels={"jumpstarter.dev/name": "composite1"},
                childs=[
                    MockPower(labels={"jumpstarter.dev/name": "power1"}),
                ],
            ),
        ],
    )
    mock.add_to_server(server)

    await server.start()

    async with grpc.aio.insecure_channel("localhost:50051") as channel:
        stub = jumpstarter_pb2_grpc.ExporterServiceStub(channel)
        client = ClientFromReports(mock.Reports(), stub=stub)

        assert await client.power0.on() == "ok"
        assert await client.composite1.power1.on() == "ok"

    await server.stop(grace=None)
