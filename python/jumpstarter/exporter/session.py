from contextlib import suppress
from dataclasses import dataclass
from uuid import UUID

from anyio import Event, TypedAttributeLookupError

from jumpstarter.common import Metadata
from jumpstarter.common.streams import StreamRequestMetadata
from jumpstarter.driver import Driver
from jumpstarter.streams import MetadataStreamAttributes, RouterStream, forward_stream
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
        self.mapping = {u: i for (u, _, _, i) in self.root_device.enumerate()}

    def add_to_server(self, server):
        jumpstarter_pb2_grpc.add_ExporterServiceServicer_to_server(self, server)
        router_pb2_grpc.add_RouterServiceServicer_to_server(self, server)

    def __getitem__(self, key: UUID):
        return self.mapping[key]

    async def GetReport(self, request, context):
        return jumpstarter_pb2.GetReportResponse(
            uuid=str(self.uuid),
            labels=self.labels,
            reports=[
                instance.report(parent=parent, name=name)
                for (_, parent, name, instance) in self.root_device.enumerate()
            ],
        )

    async def DriverCall(self, request, context):
        return await self[UUID(request.uuid)].DriverCall(request, context)

    async def StreamingDriverCall(self, request, context):
        async for v in self[UUID(request.uuid)].StreamingDriverCall(request, context):
            yield v

    async def Stream(self, _request_iterator, context):
        request = StreamRequestMetadata(**dict(list(context.invocation_metadata()))).request

        async with self[request.uuid].Stream(request, context) as stream:
            metadata = []
            with suppress(TypedAttributeLookupError):
                metadata.extend(stream.extra(MetadataStreamAttributes.metadata).items())
            await context.send_initial_metadata(metadata)

            async with RouterStream(context=context) as remote:
                async with forward_stream(remote, stream):
                    event = Event()
                    context.add_done_callback(lambda _: event.set())
                    await event.wait()
