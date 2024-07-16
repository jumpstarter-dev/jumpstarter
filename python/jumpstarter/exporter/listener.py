from jumpstarter.v1 import (
    router_pb2,
    router_pb2_grpc,
    jumpstarter_pb2,
    jumpstarter_pb2_grpc,
)
from dataclasses import dataclass
import itertools
import anyio
import grpc


@dataclass
class Listener:
    stub: jumpstarter_pb2_grpc.ControllerServiceStub

    def __init__(self, channel):
        self.stub = jumpstarter_pb2_grpc.ControllerServiceStub(channel)

    async def handle(self, request):
        credentials = grpc.composite_channel_credentials(
            grpc.local_channel_credentials(),
            grpc.access_token_call_credentials(request.router_token),
        )

        async with grpc.aio.secure_channel(
            request.router_endpoint, credentials
        ) as channel:
            router = router_pb2_grpc.RouterServiceStub(channel)

            async with await anyio.connect_tcp("localhost", 50051) as stream:

                async def local_to_router():
                    async for payload in stream:
                        yield router_pb2.StreamRequest(payload=payload)

                # router_to_local
                try:
                    async for frame in router.Stream(local_to_router()):
                        await stream.send(frame.payload)
                except grpc.aio.AioRpcError:
                    # TODO: handle connection error
                    pass
                finally:
                    await stream.send_eof()

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
