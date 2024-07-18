from jumpstarter.v1 import (
    jumpstarter_pb2,
    jumpstarter_pb2_grpc,
    router_pb2_grpc,
)
from jumpstarter.common.streams import forward_server_stream, create_memory_stream
from jumpstarter.common import Metadata
from jumpstarter.drivers import DriverBase, ContextStore
from jumpstarter.drivers.composite import Composite
from uuid import UUID, uuid4
from dataclasses import dataclass, asdict, is_dataclass
from google.protobuf import struct_pb2, json_format
from collections import ChainMap


@dataclass(kw_only=True)
class Session(
    jumpstarter_pb2_grpc.ExporterServiceServicer,
    router_pb2_grpc.RouterServiceServicer,
    Metadata,
):
    root_device: DriverBase
    mapping: dict[UUID, DriverBase]

    def __init__(self, *args, root_device, **kwargs):
        super().__init__(*args, **kwargs)

        self.root_device = root_device
        self.mapping = self.root_device.mapping()

    def add_to_server(self, server):
        jumpstarter_pb2_grpc.add_ExporterServiceServicer_to_server(self, server)
        router_pb2_grpc.add_RouterServiceServicer_to_server(self, server)

    def __getitem__(self, key: UUID):
        return self.mapping[key]

    async def GetReport(self, request, context):
        return jumpstarter_pb2.GetReportResponse(
            uuid=str(self.uuid),
            labels=self.labels,
            reports=self.root_device.reports(),
        )

    async def DriverCall(self, request, context):
        args = [json_format.MessageToDict(arg) for arg in request.args]
        result = await self[UUID(request.uuid)].call(request.method, args)
        return jumpstarter_pb2.DriverCallResponse(
            uuid=str(uuid4()),
            result=json_format.ParseDict(
                asdict(result) if is_dataclass(result) else result, struct_pb2.Value()
            ),
        )

    async def StreamingDriverCall(self, request, context):
        args = [json_format.MessageToDict(arg) for arg in request.args]
        async for result in self[UUID(request.uuid)].streaming_call(
            request.method, args
        ):
            yield jumpstarter_pb2.StreamingDriverCallResponse(
                uuid=str(uuid4()),
                result=json_format.ParseDict(
                    asdict(result) if is_dataclass(result) else result,
                    struct_pb2.Value(),
                ),
            )

    async def Stream(self, request_iterator, context):
        metadata = dict(context.invocation_metadata())

        uuid = UUID(metadata["uuid"])

        match metadata["kind"]:
            case "device":
                device = self[uuid]
                async with device.connect() as stream:
                    async for v in forward_server_stream(request_iterator, stream):
                        yield v
            case "resource":
                client_stream, device_stream = create_memory_stream()

                try:
                    ContextStore.get().conns[uuid] = device_stream
                    async with client_stream:
                        async for v in forward_server_stream(
                            request_iterator, client_stream
                        ):
                            yield v
                finally:
                    del ContextStore.get().conns[uuid]
