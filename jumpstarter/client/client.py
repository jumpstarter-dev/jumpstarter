from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc
from jumpstarter.drivers.power.base import PowerStub
from jumpstarter.drivers.serial.base import SerialStub
from google.protobuf import empty_pb2, struct_pb2, json_format
from dataclasses import dataclass


@dataclass
class Client:
    stub: jumpstarter_pb2_grpc.ExporterServiceStub

    def __init__(self, channel):
        self.stub = jumpstarter_pb2_grpc.ExporterServiceStub(channel)

    def GetReport(self):
        return self.stub.GetReport(empty_pb2.Empty())

    def GetDevice(self, report: jumpstarter_pb2.DeviceReport):
        match report.driver_interface:
            case "power":
                return PowerStub(self.stub, report.device_uuid, report.labels)
            case "serial":
                return SerialStub(self.stub, report.device_uuid, report.labels)
            case _:
                raise NotImplementedError
