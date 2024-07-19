from jumpstarter.drivers.power import PowerReading, MockPower, PowerClient
from jumpstarter.v1 import jumpstarter_pb2_grpc
import pytest
import grpc

pytestmark = pytest.mark.anyio


async def test_drivers_power_mock():
    server = grpc.aio.server()
    server.add_insecure_port("localhost:50051")

    mock = MockPower(labels={"jumpstarter.dev/name": "mock"})
    mock.add_to_server(server)

    await server.start()

    async with grpc.aio.insecure_channel("localhost:50051") as channel:
        client = PowerClient(
            uuid=mock.uuid,
            labels=mock.labels,
            channel=channel,
        )

        assert await client.on() == "ok"
        assert await client.off() == "ok"

        assert [reading async for reading in client.read()] == [
            PowerReading(voltage=0.0, current=0.0),
            PowerReading(voltage=5.0, current=2.0),
        ]

    await server.stop(grace=None)
