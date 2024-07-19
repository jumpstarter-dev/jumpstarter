from jumpstarter.exporter import Session
from jumpstarter.client import Client

# from jumpstarter.drivers.power import PowerReading
# from jumpstarter.drivers.serial import MockSerial
# from jumpstarter.drivers.storage import MockStorageMux
from jumpstarter.drivers.network import EchoNetwork
from jumpstarter.drivers.composite import Composite
from jumpstarter.drivers.power import PowerReading, MockPower
from jumpstarter.drivers import ContextStore, Store
from dataclasses import asdict
import os
import subprocess
import shutil
import tempfile
import pytest
import anyio
import grpc
import sys

pytestmark = pytest.mark.anyio


@pytest.fixture
async def setup_client(request, anyio_backend):
    ContextStore.set(Store())

    server = grpc.aio.server()

    try:
        e = Session(
            labels={"jumpstarter.dev/name": "exporter"}, root_device=request.param
        )
    except FileNotFoundError:
        pytest.skip("fail to find required devices")

    e.add_to_server(server)

    server.add_insecure_port("localhost:50051")
    await server.start()

    client = Client(grpc.aio.insecure_channel("localhost:50051"))
    await client.sync()
    yield client

    await server.stop(grace=None)
    await server.wait_for_termination()


# @pytest.mark.skipif(shutil.which("iperf3") is None, reason="iperf3 not available")
# @pytest.mark.parametrize(
#     "setup_client",
#     [
#         TcpNetwork(
#             labels={"jumpstarter.dev/name": "iperf3"},
#             host="127.0.0.1",
#             port=5201,
#         )
#     ],
#     indirect=True,
# )
# async def test_tcp_network(setup_client):
#     client = setup_client
#
#     listener = await anyio.create_tcp_listener(local_port=8001)
#
#     async with await anyio.open_process(
#         ["iperf3", "-s"],
#         stdout=subprocess.DEVNULL,
#         stderr=subprocess.DEVNULL,
#     ) as server:
#         async with client.Forward(listener, client.iperf3):
#             await anyio.run_process(
#                 [
#                     "iperf3",
#                     "-c",
#                     "127.0.0.1",
#                     "-p",
#                     "8001",
#                     "-t",
#                     "1",
#                 ],
#                 stdout=sys.stdout,
#                 stderr=sys.stderr,
#             )
#         server.terminate()


@pytest.mark.parametrize(
    "setup_client",
    [EchoNetwork(labels={"jumpstarter.dev/name": "echo"})],
    indirect=True,
)
async def test_echo_network(setup_client):
    client = setup_client

    async with client.root.connect() as stream:
        await stream.send(b"hello")
        assert await stream.receive() == b"hello"


@pytest.mark.parametrize(
    "setup_client",
    [
        Composite(
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
    ],
    indirect=True,
)
async def test_exporter_mock(setup_client):
    client = setup_client

    assert await client.root.composite1.power1.on() == "ok"
    assert [reading async for reading in client.root.composite1.power1.read()] == [
        PowerReading(voltage=0.0, current=0.0),
        PowerReading(voltage=5.0, current=2.0),
    ]


#
#     def baudrate():
#         client.root.serial.baudrate = 115200
#         assert client.root.serial.baudrate == 115200
#
#     await anyio.to_thread.run_sync(baudrate)
#
#     assert await client.root.composite.power.on() == "ok"
#     assert await anext(client.root.composite.power.read()) == asdict(
#         PowerReading(5.0, 2.0)
#     )
#
#     assert await client.root.composite.composite.power.on() == "ok"
#     assert await anext(client.root.composite.composite.power.read()) == asdict(
#         PowerReading(5.0, 2.0)
#     )
#
#     with tempfile.NamedTemporaryFile(delete=False) as tempf:
#         tempf.write(b"thisisatestfile")
#         tempf.close()
#
#         async with client.LocalFile(tempf.name) as file:
#             await client.root.storage.write(file)
#
#         os.unlink(tempf.name)
#
#     async with client.Stream(client.root.echo) as stream:
#         await stream.send(b"test")
#         assert await stream.receive() == b"test"
