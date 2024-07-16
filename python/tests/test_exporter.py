from jumpstarter.drivers.power import MockPower
from jumpstarter.drivers.power import PowerReading
from jumpstarter.drivers.serial import MockSerial
from jumpstarter.drivers.storage import LocalStorageTempdir, MockStorageMux
from jumpstarter.drivers.network import TcpNetwork, EchoNetwork
from jumpstarter.drivers.composite import Composite, Dutlink
from dataclasses import asdict
import subprocess
import pytest
import anyio
import sys

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "setup_exporter",
    [
        lambda session: [
            MockPower(session=session, labels={"jumpstarter.dev/name": "power"}),
            MockSerial(session=session, labels={"jumpstarter.dev/name": "serial"}),
            MockStorageMux(session=session, labels={"jumpstarter.dev/name": "storage"}),
            EchoNetwork(session=session, labels={"jumpstarter.dev/name": "echo"}),
            TcpNetwork(
                session=session,
                labels={"jumpstarter.dev/name": "iperf3"},
                host="127.0.0.1",
                port=5201,
            ),
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
async def test_exporter_mock(setup_exporter):
    client = setup_exporter

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

    async with client.LocalFile(
        "/home/nickcao/Downloads/archlinux-2024.07.01-x86_64.iso"
    ) as file:
        await client.storage.write(file)

    async with client.Stream(client.echo) as stream:
        await stream.send(b"test")
        assert await stream.receive() == b"test"

    listener = await anyio.create_tcp_listener(local_port=0)
    print(listener.listeners[0].local_port)
    async with client.Forward(listener, client.iperf3):
        try:
            await anyio.run_process(
                ["iperf3", "-c", "127.0.0.1", "-p", "8001", "-t", "1"],
                stdout=sys.stdout,
                stderr=subprocess.STDOUT,
            )
        except:
            pass


@pytest.mark.parametrize(
    "setup_exporter",
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
async def test_exporter_dutlink(setup_exporter):
    client = setup_exporter

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
