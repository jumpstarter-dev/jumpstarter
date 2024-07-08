from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc
from jumpstarter.drivers import DriverBase
from uuid import UUID, uuid4
from dataclasses import dataclass, asdict, is_dataclass
from google.protobuf import struct_pb2, json_format


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
            device_report=[device.report() for device in self.devices.values()],
        )

    def DriverCall(self, request, context):
        args = [json_format.MessageToDict(arg) for arg in request.args]
        result = self.devices[UUID(request.device_uuid)].call(
            request.driver_method, args
        )
        return jumpstarter_pb2.DriverCallResponse(
            call_uuid=str(uuid4()),
            result=json_format.ParseDict(
                asdict(result) if is_dataclass(result) else result, struct_pb2.Value()
            ),
        )

    def StreamingDriverCall(self, request, context):
        args = [json_format.MessageToDict(arg) for arg in request.args]
        for result in self.devices[UUID(request.device_uuid)].streaming_call(
            request.driver_method, args
        ):
            yield jumpstarter_pb2.StreamingDriverCallResponse(
                call_uuid=str(uuid4()),
                result=json_format.ParseDict(
                    asdict(result) if is_dataclass(result) else result,
                    struct_pb2.Value(),
                ),
            )
