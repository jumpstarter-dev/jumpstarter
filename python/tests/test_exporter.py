from jumpstarter.exporter import Exporter
from jumpstarter.drivers.power.mock import MockPower
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc
from google.protobuf import empty_pb2, struct_pb2, json_format
from concurrent import futures
import grpc


def test_exporter():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    e = Exporter(devices=[MockPower()])
    e.add_to_server(server)

    server.add_insecure_port("localhost:50051")
    server.start()

    with grpc.insecure_channel("localhost:50051") as channel:
        stub = jumpstarter_pb2_grpc.ExporterServiceStub(channel)
        report = stub.GetReport(empty_pb2.Empty())

        def drivercall(device_uuid, driver_method, *args):
            return json_format.MessageToDict(
                stub.DriverCall(
                    jumpstarter_pb2.DriverCallRequest(
                        device_uuid=device_uuid,
                        driver_method=driver_method,
                        args=[
                            json_format.ParseDict(arg, struct_pb2.Value())
                            for arg in args
                        ],
                    )
                ).result
            )

        for device in report.device_report:
            match device.driver_interface:
                case "power":
                    assert drivercall(device.device_uuid, "on") == True
                    assert drivercall(device.device_uuid, "read") == {
                        "apparent_power": 10.0,
                        "current": 2.0,
                        "voltage": 5.0,
                    }
                case _:
                    raise NotImplementedError

    server.stop(grace=None)
    server.wait_for_termination()
