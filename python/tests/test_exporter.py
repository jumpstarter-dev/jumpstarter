from jumpstarter.client import Client
from jumpstarter.drivers.power import PowerReading
from dataclasses import asdict
import pytest
import grpc


def test_exporter(setup_exporter):
    with grpc.insecure_channel("localhost:50051") as channel:
        client = Client(channel)

        assert client.power.on() == "ok"
        assert next(client.power.read()) == asdict(PowerReading(5.0, 2.0))

        client.serial.baudrate = 115200
        assert client.serial.baudrate == 115200

        assert client.composite.power.on() == "ok"
        assert next(client.composite.power.read()) == asdict(PowerReading(5.0, 2.0))

        client.dutlink.power.on()
        client.dutlink.power.off()
        assert client.dutlink.serial.write("version\r\n") == 9
        assert client.dutlink.serial.read(13) == "version\r\n0.07"

        client.dutlink.storage.off()
        client.dutlink.storage.host()
        client.dutlink.storage.dut()
        with pytest.raises(Exception):
            # permission denied
            client.dutlink.storage.write("/dev/null")
        client.dutlink.storage.off()
