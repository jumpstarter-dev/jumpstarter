from jumpstarter.exporter import Exporter
from jumpstarter.client import Client
from jumpstarter.drivers.power.base import PowerReading
from jumpstarter.drivers.power.mock import MockPower
from jumpstarter.drivers.serial.mock import MockSerial
from concurrent import futures
from dataclasses import asdict
import grpc


def test_exporter():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    e = Exporter(
        devices=[
            MockPower(
                labels={
                    "jumpstarter.dev/name": "power",
                }
            ),
            MockSerial(
                labels={
                    "jumpstarter.dev/name": "serial",
                }
            ),
        ]
    )
    e.add_to_server(server)

    server.add_insecure_port("localhost:50051")
    server.start()

    with grpc.insecure_channel("localhost:50051") as channel:
        client = Client(channel)

        assert client.power.on()
        assert client.power.read() == asdict(PowerReading(5.0, 2.0))

        client.serial.baudrate = 115200
        assert client.serial.baudrate == 115200

    server.stop(grace=None)
    server.wait_for_termination()
