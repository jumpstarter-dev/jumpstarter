from jumpstarter.common.streams import connect_router_stream
from jumpstarter.v1 import jumpstarter_pb2_grpc, router_pb2
from dataclasses import dataclass
from contextlib import asynccontextmanager
from tempfile import TemporaryDirectory
from pathlib import Path
from anyio import create_tcp_listener, create_task_group
from uuid import UUID
import grpc


@dataclass(kw_only=True)
class Proxy:
    uuid: UUID
    stub: jumpstarter_pb2_grpc.ControllerServiceStub

    async def handle(client):
        response = await self.stub.Dial(router_pb2.DialRequest(uuid=str(self.uuid)))

        async with client:
            await connect_router_stream(
                response.router_endpoint, response.router_token, client
            )

    @asynccontextmanager
    async def channel(self):
        credentials = grpc.composite_channel_credentials(
            grpc.local_channel_credentials(),
        )

        with TemporaryDirectory() as tempdir:
            socketpath = Path(tempdir) / "socket"

            async with await create_unix_listener(socketpath) as listener:
                async with create_task_group() as tg:
                    tg.start_soon(listener.serve, self.handle, tg)

                    async with grpc.aio.secure_channel(
                        f"unix://{socketpath}", credentials
                    ) as channel:
                        try:
                            yield channel
                        finally:
                            tg.cancel_scope.cancel()
