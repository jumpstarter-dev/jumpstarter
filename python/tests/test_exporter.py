from jumpstarter.drivers.power import PowerReading
from jumpstarter.drivers.power import MockPower
from jumpstarter.drivers.serial import MockSerial
from jumpstarter.drivers.storage import LocalStorageTempdir
from jumpstarter.drivers.composite import Composite, Dutlink
from dataclasses import asdict
import pytest


@pytest.mark.parametrize(
    "setup_exporter",
    [
        lambda session: [
            MockPower(session=session, labels={"jumpstarter.dev/name": "power"}),
            MockSerial(session=session, labels={"jumpstarter.dev/name": "serial"}),
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
                ],
            ),
        ]
    ],
    indirect=True,
)
def test_exporter_mock(setup_exporter):
    client = setup_exporter

    assert client.power.on() == "ok"
    assert next(client.power.read()) == asdict(PowerReading(5.0, 2.0))

    client.serial.baudrate = 115200
    assert client.serial.baudrate == 115200

    assert client.composite.power.on() == "ok"
    assert next(client.composite.power.read()) == asdict(PowerReading(5.0, 2.0))


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
def test_exporter_dutlink(setup_exporter):
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
