from jumpstarter.exporter import Exporter
from jumpstarter.drivers.power import MockPower
from jumpstarter.drivers.serial import MockSerial
from jumpstarter.drivers.composite import Composite, Dutlink
from concurrent import futures
import pytest
import grpc


@pytest.fixture(scope="module")
def setup_exporter():
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
            Dutlink(labels={"jumpstarter.dev/name": "dutlink"}, serial="c415a913"),
        ]
    )
    e.add_to_server(server)

    server.add_insecure_port("localhost:50051")
    server.start()

    yield None

    server.stop(grace=None)
    server.wait_for_termination()
