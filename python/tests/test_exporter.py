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

    e = Exporter(devices=[MockPower(), MockSerial()])
    e.add_to_server(server)

    server.add_insecure_port("localhost:50051")
    server.start()

    with grpc.insecure_channel("localhost:50051") as channel:
        client = Client(channel)

        report = client.GetReport()

        for device in report.device_report:
            stub = client.GetDevice(device)
            match device.driver_interface:
                case "power":
                    assert stub.on() is True
                    assert stub.read() == asdict(PowerReading(5.0, 2.0))
                case "serial":
                    stub.baudrate = 115200
                    assert stub.baudrate == 115200
                case _:
                    raise NotImplementedError

    server.stop(grace=None)
    server.wait_for_termination()
