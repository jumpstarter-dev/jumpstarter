from jumpstarter.v1 import (
    jumpstarter_pb2,
    jumpstarter_pb2_grpc,
    router_pb2_grpc,
)
from jumpstarter.common.streams import forward_server_stream, create_memory_stream
from jumpstarter.common import Metadata
from jumpstarter.drivers import Driver, ContextStore
from uuid import UUID
from dataclasses import dataclass


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
        self.mapping = dict(self.root_device.items())

    def add_to_server(self, server):
        jumpstarter_pb2_grpc.add_ExporterServiceServicer_to_server(self, server)
        router_pb2_grpc.add_RouterServiceServicer_to_server(self, server)

    def __getitem__(self, key: UUID):
        return self.mapping[key]

    async def GetReport(self, request, context):
        return jumpstarter_pb2.GetReportResponse(
            uuid=str(self.uuid),
            labels=self.labels,
            reports=self.root_device.Reports(),
        )

    async def DriverCall(self, request, context):
        return await self[UUID(request.uuid)].DriverCall(request, context)

    async def StreamingDriverCall(self, request, context):
        async for v in self[UUID(request.uuid)].StreamingDriverCall(request, context):
            yield v

    async def Stream(self, request_iterator, context):
        metadata = dict(context.invocation_metadata())

        uuid = UUID(metadata["uuid"])

        match metadata["kind"]:
            case "device":
                device = self[uuid]
                async for v in device.Stream(request_iterator, context):
                    yield v
            case "resource":
                client_stream, device_stream = create_memory_stream()

                try:
                    ContextStore.get().conns[uuid] = device_stream
                    async with client_stream:
                        async for v in forward_server_stream(request_iterator, client_stream):
                            yield v
                finally:
                    del ContextStore.get().conns[uuid]
