from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc
from jumpstarter.drivers.base import DriverBase
from uuid import UUID, uuid4
from dataclasses import dataclass
from typing import List
import grpc


@dataclass
class Exporter(jumpstarter_pb2_grpc.ExporterServiceServicer):
    uuid: UUID
    labels: dict[str, str]
    devices: dict[UUID, DriverBase]

    def __init__(self, uuid=None, labels={}, devices=[]):
        self.uuid = uuid or uuid4()
        self.labels = labels
        self.devices = {device.uuid: device for device in devices}

    def add_to_server(self, server):
        jumpstarter_pb2_grpc.add_ExporterServiceServicer_to_server(self, server)

    def GetReport(self, request, context):
        return jumpstarter_pb2.GetReportResponse(
            uuid=str(self.uuid),
            labels=self.labels,
            device_report=[
                jumpstarter_pb2.DeviceReport(
                    device_uuid=str(device.uuid),
                    driver_interface=device.interface,
                    labels=device.labels,
                )
                for device in self.devices.values()
            ],
        )

    def DriverCall(self, request, context):
        result = self.devices[UUID(request.device_uuid)].call(
            request.driver_method, request.args
        )
        # TODO: use grpc native json type
        return jumpstarter_pb2.DriverCallResponse(
            call_uuid=str(uuid4()), json_result=str(result)
        )
