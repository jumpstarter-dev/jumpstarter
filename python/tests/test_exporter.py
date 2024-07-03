from jumpstarter.exporter import Exporter
from jumpstarter.client import Client
from jumpstarter.drivers.power.base import Power, PowerReading
from jumpstarter.drivers.power.mock import MockPower
from jumpstarter.drivers.serial.base import Serial
from jumpstarter.drivers.serial.mock import MockSerial
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc
from google.protobuf import empty_pb2, struct_pb2, json_format
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
            stub = client.GetDevice(device.driver_interface, device.device_uuid)
            match device.driver_interface:
                case "power":
                    assert stub.on() == True
                    assert stub.read() == asdict(PowerReading(5.0, 2.0))
                case "serial":
                    pass
                case _:
                    raise NotImplementedError

    server.stop(grace=None)
    server.wait_for_termination()
