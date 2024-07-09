from jumpstarter.exporter import Exporter
from jumpstarter.client import Client
from jumpstarter.drivers.power import PowerReading, MockPower
from jumpstarter.drivers.serial import MockSerial
from jumpstarter.drivers.composite import Composite
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
            Composite(
                labels={
                    "jumpstarter.dev/name": "composite",
                },
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
                ],
            ),
        ]
    )
    e.add_to_server(server)

    server.add_insecure_port("localhost:50051")
    server.start()

    with grpc.insecure_channel("localhost:50051") as channel:
        client = Client(channel)

        assert client.power.on()
        assert next(client.power.read()) == asdict(PowerReading(5.0, 2.0))

        client.serial.baudrate = 115200
        assert client.serial.baudrate == 115200

        assert client.composite.power.on()
        assert next(client.composite.power.read()) == asdict(PowerReading(5.0, 2.0))

    server.stop(grace=None)
    server.wait_for_termination()
