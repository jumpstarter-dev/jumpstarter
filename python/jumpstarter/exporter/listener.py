from jumpstarter.v1 import (
    jumpstarter_pb2,
    jumpstarter_pb2_grpc,
)
from jumpstarter.common.streams import connect_router_stream
from dataclasses import dataclass
import itertools
import anyio


@dataclass
class Listener:
    stub: jumpstarter_pb2_grpc.ControllerServiceStub

    def __init__(self, channel):
        self.stub = jumpstarter_pb2_grpc.ControllerServiceStub(channel)

    async def handle(self, request):
        async with await anyio.connect_tcp("localhost", 50051) as stream:
            await connect_router_stream(
                request.router_endpoint, request.router_token, stream
            )

    async def serve(self, exporter):
        await self.stub.Register(
            jumpstarter_pb2.RegisterRequest(
                uuid=str(exporter.uuid),
                labels=exporter.labels,
                device_report=itertools.chain(
                    *[device.reports() for device in exporter.session.devices]
                ),
            )
        )

        async with anyio.create_task_group() as tg:
            async for request in self.stub.Listen(jumpstarter_pb2.ListenRequest()):
                tg.start_soon(self.handle, request)
