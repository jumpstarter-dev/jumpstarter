from jumpstarter.drivers.power import MockPower
from jumpstarter.exporter import Exporter, ExporterSession
from jumpstarter.client import Client
from jumpstarter.drivers.power import PowerReading
from jumpstarter.drivers.serial import MockSerial
from jumpstarter.drivers.storage import LocalStorageTempdir, MockStorageMux
from jumpstarter.drivers.network import TcpNetwork, EchoNetwork
from jumpstarter.drivers.composite import Composite, Dutlink
from dataclasses import asdict
import os
import subprocess
import shutil
import tempfile
import pytest
import anyio
import grpc
import json
import sys

pytestmark = pytest.mark.anyio


@pytest.fixture
async def setup_client(request, anyio_backend):
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


@pytest.mark.skipif(shutil.which("iperf3") is None, reason="iperf3 not available")
@pytest.mark.parametrize(
    "setup_client",
    [
        lambda session: [
            TcpNetwork(
                session=session,
                labels={"jumpstarter.dev/name": "iperf3"},
                host="127.0.0.1",
                port=5201,
            ),
        ]
    ],
    indirect=True,
)
async def test_tcp_network(setup_client):
    client = setup_client

    listener = await anyio.create_tcp_listener(local_port=8001)

    async with await anyio.open_process(
        ["iperf3", "-s"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as server:
        async with client.Forward(listener, client.iperf3):
            await anyio.run_process(
                [
                    "iperf3",
                    "-c",
                    "127.0.0.1",
                    "-p",
                    "8001",
                    "-t",
                    "1",
                ],
                stdout=sys.stdout,
                stderr=sys.stderr,
            )
        server.terminate()


@pytest.mark.parametrize(
    "setup_client",
    [
        lambda session: [
            MockPower(session=session, labels={"jumpstarter.dev/name": "power"}),
            MockSerial(session=session, labels={"jumpstarter.dev/name": "serial"}),
            MockStorageMux(session=session, labels={"jumpstarter.dev/name": "storage"}),
            EchoNetwork(session=session, labels={"jumpstarter.dev/name": "echo"}),
            Composite(
                session=session,
                labels={"jumpstarter.dev/name": "composite"},
                devices=[
                    MockPower(
                        session=session, labels={"jumpstarter.dev/name": "power"}
                    ),
                    MockSerial(
                        session=session, labels={"jumpstarter.dev/name": "serial"}
                    ),
                    Composite(
                        session=session,
                        labels={"jumpstarter.dev/name": "composite"},
                        devices=[
                            MockPower(
                                session=session,
                                labels={"jumpstarter.dev/name": "power"},
                            ),
                            MockSerial(
                                session=session,
                                labels={"jumpstarter.dev/name": "serial"},
                            ),
                        ],
                    ),
                ],
            ),
        ]
    ],
    indirect=True,
)
async def test_exporter_mock(setup_client):
    client = setup_client

    assert await client.power.on() == "ok"
    assert await anext(client.power.read()) == asdict(PowerReading(5.0, 2.0))

    def baudrate():
        client.serial.baudrate = 115200
        assert client.serial.baudrate == 115200

    await anyio.to_thread.run_sync(baudrate)

    assert await client.composite.power.on() == "ok"
    assert await anext(client.composite.power.read()) == asdict(PowerReading(5.0, 2.0))

    assert await client.composite.composite.power.on() == "ok"
    assert await anext(client.composite.composite.power.read()) == asdict(
        PowerReading(5.0, 2.0)
    )

    with tempfile.NamedTemporaryFile(delete=False) as tempf:
        tempf.write(b"thisisatestfile")
        tempf.close()

        async with client.LocalFile(tempf.name) as file:
            await client.storage.write(file)

        os.unlink(tempf.name)

    async with client.Stream(client.echo) as stream:
        await stream.send(b"test")
        assert await stream.receive() == b"test"


@pytest.mark.parametrize(
    "setup_client",
    [
        lambda session: [
            LocalStorageTempdir(
                session=session, labels={"jumpstarter.dev/name": "tempdir"}
            ),
            Dutlink(
                session=session, labels={"jumpstarter.dev/name": "dutlink"}, serial=None
            ),
        ]
    ],
    indirect=True,
)
async def test_exporter_dutlink(setup_client):
    client = setup_client

    client.dutlink.power.on()
    client.dutlink.power.off()
    assert client.dutlink.serial.write("version\r\n") == 9
    assert client.dutlink.serial.read(13) == "version\r\n0.07"

    client.tempdir.download(
        "https://dl-cdn.alpinelinux.org/alpine/v3.20/releases/x86_64/alpine-virt-3.20.1-x86_64.iso",
        {},
        "alpine.iso",
    )

    alpine = client.tempdir.open("alpine.iso", "rb")

    client.dutlink.storage.off()
    client.dutlink.storage.dut()
    client.dutlink.storage.write(alpine)
    client.dutlink.storage.off()
