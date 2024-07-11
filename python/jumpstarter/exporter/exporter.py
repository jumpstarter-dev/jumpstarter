from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc
from jumpstarter.common import Metadata
from jumpstarter.drivers import DriverBase, Session
from jumpstarter.drivers.composite import Composite
from uuid import UUID, uuid4
from dataclasses import dataclass, asdict, is_dataclass
from google.protobuf import struct_pb2, json_format
from typing import List
from collections import ChainMap
import itertools


@dataclass(kw_only=True)
class ExporterSession:
    session: Session
    devices: List[DriverBase]
    mapping: dict[UUID, DriverBase]

    def __init__(self, devices_factory):
        self.session = Session()
        self.devices = devices_factory(self.session)
        self.mapping = {}

        def subdevices(device):
            if isinstance(device, Composite):
                return dict(
                    ChainMap(*[subdevices(subdevice) for subdevice in device.devices])
                )
            else:
                return {device.uuid: device}

        for device in self.devices:
            self.mapping |= subdevices(device)


@dataclass(kw_only=True)
class Exporter(jumpstarter_pb2_grpc.ExporterServiceServicer, Metadata):
    session: ExporterSession

    def add_to_server(self, server):
        jumpstarter_pb2_grpc.add_ExporterServiceServicer_to_server(self, server)

    def GetReport(self, request, context):
        return jumpstarter_pb2.GetReportResponse(
            uuid=str(self.uuid),
            labels=self.labels,
            device_report=itertools.chain(
                *[device.reports() for device in self.session.devices]
            ),
        )

    def DriverCall(self, request, context):
        args = [json_format.MessageToDict(arg) for arg in request.args]
        result = self.session.mapping[UUID(request.device_uuid)].call(
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
        for result in self.session.mapping[UUID(request.device_uuid)].streaming_call(
            request.driver_method, args
        ):
            yield jumpstarter_pb2.StreamingDriverCallResponse(
                call_uuid=str(uuid4()),
                result=json_format.ParseDict(
                    asdict(result) if is_dataclass(result) else result,
                    struct_pb2.Value(),
                ),
            )
