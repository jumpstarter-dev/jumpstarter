from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc
from jumpstarter.drivers.power.base import PowerStub
from jumpstarter.drivers.serial.base import SerialStub
from google.protobuf import empty_pb2
from dataclasses import dataclass


@dataclass
class Client:
    stub: jumpstarter_pb2_grpc.ExporterServiceStub

    def __init__(self, channel):
        self.stub = jumpstarter_pb2_grpc.ExporterServiceStub(channel)
        for device in self.GetReport().device_report:
            stub = self.GetDevice(device)
            setattr(self, stub.labels["jumpstarter.dev/name"], stub)

    def GetReport(self):
        return self.stub.GetReport(empty_pb2.Empty())

    def GetDevice(self, report: jumpstarter_pb2.DeviceReport):
        match report.driver_interface:
            case "power":
                return PowerStub(
                    stub=self.stub, uuid=report.device_uuid, labels=report.labels
                )
            case "serial":
                return SerialStub(
                    stub=self.stub, uuid=report.device_uuid, labels=report.labels
                )
            case _:
                raise NotImplementedError
