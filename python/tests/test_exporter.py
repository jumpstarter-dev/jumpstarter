from jumpstarter.exporter import Exporter
from jumpstarter.drivers.power.mock import MockPower
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc
from google.protobuf import empty_pb2
from concurrent import futures
import grpc


def test_exporter():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    e = Exporter(devices=[MockPower(), MockPower()])
    e.add_to_server(server)

    server.add_insecure_port("localhost:50051")
    server.start()

    with grpc.insecure_channel("localhost:50051") as channel:
        stub = jumpstarter_pb2_grpc.ExporterServiceStub(channel)
        report = stub.GetReport(empty_pb2.Empty())
        for device in report.device_report:
            match device.driver_interface:
                case "power":
                    assert (
                        stub.DriverCall(
                            jumpstarter_pb2.DriverCallRequest(
                                device_uuid=device.device_uuid,
                                driver_method="read",
                            )
                        ).json_result
                        == "<PowerReading: 5.0V 2.0A 10.0W>"
                    )
                case _:
                    raise NotImplementedError

    server.stop(grace=None)
    server.wait_for_termination()
