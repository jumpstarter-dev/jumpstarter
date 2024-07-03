from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc
from google.protobuf import empty_pb2, struct_pb2, json_format
from dataclasses import dataclass


@dataclass
class Client:
    stub: jumpstarter_pb2_grpc.ExporterServiceStub

    def __init__(self, channel):
        self.stub = jumpstarter_pb2_grpc.ExporterServiceStub(channel)

    def GetReport(self):
        return self.stub.GetReport(empty_pb2.Empty())

    def DriverCall(self, device_uuid: str, driver_method: str, *args):
        return json_format.MessageToDict(
            self.stub.DriverCall(
                jumpstarter_pb2.DriverCallRequest(
                    device_uuid=device_uuid,
                    driver_method=driver_method,
                    args=[
                        json_format.ParseDict(arg, struct_pb2.Value()) for arg in args
                    ],
                )
            ).result
        )
