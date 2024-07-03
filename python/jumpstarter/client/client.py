from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc
from jumpstarter.drivers.stub import DriverStub
from google.protobuf import empty_pb2, struct_pb2, json_format
from dataclasses import dataclass


@dataclass
class Client:
    stub: jumpstarter_pb2_grpc.ExporterServiceStub

    def __init__(self, channel):
        self.stub = jumpstarter_pb2_grpc.ExporterServiceStub(channel)

    def GetReport(self):
        return self.stub.GetReport(empty_pb2.Empty())

    def GetDevice(self, cls, device_uuid):
        return DriverStub(self.stub, cls, device_uuid)
