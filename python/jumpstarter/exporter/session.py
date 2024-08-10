from dataclasses import dataclass
from uuid import UUID

from jumpstarter.common import Metadata
from jumpstarter.drivers.base import Driver
from jumpstarter.drivers.streams import StreamRequest
from jumpstarter.v1 import (
    jumpstarter_pb2,
    jumpstarter_pb2_grpc,
    router_pb2_grpc,
)


@dataclass(kw_only=True)
class Session(
    jumpstarter_pb2_grpc.ExporterServiceServicer,
    router_pb2_grpc.RouterServiceServicer,
    Metadata,
):
    root_device: Driver
    mapping: dict[UUID, Driver]

    def __init__(self, *args, root_device, **kwargs):
        super().__init__(*args, **kwargs)

        self.root_device = root_device
        self.mapping = {u: i for (u, p, i) in self.root_device.items()}

    def add_to_server(self, server):
        jumpstarter_pb2_grpc.add_ExporterServiceServicer_to_server(self, server)
        router_pb2_grpc.add_RouterServiceServicer_to_server(self, server)

    def __getitem__(self, key: UUID):
        return self.mapping[key]

    async def GetReport(self, request, context):
        response = await self.root_device.GetReport(request, context)

        return jumpstarter_pb2.GetReportResponse(
            uuid=str(self.uuid),
            labels=self.labels,
            reports=response.reports,
        )

    async def DriverCall(self, request, context):
        return await self[UUID(request.uuid)].DriverCall(request, context)

    async def StreamingDriverCall(self, request, context):
        async for v in self[UUID(request.uuid)].StreamingDriverCall(request, context):
            yield v

    async def Stream(self, request_iterator, context):
        metadata = dict(context.invocation_metadata())

        request = StreamRequest.validate_json(metadata["request"], strict=True)

        async for v in self[request.uuid].Stream(request_iterator, context):
            yield v
