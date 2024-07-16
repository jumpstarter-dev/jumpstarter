from jumpstarter.common.streams import connect_router_stream
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc
from dataclasses import dataclass
from contextlib import asynccontextmanager
from tempfile import TemporaryDirectory
from pathlib import Path
from anyio import create_unix_listener, create_task_group
from uuid import UUID
import grpc


@dataclass(kw_only=True)
class Proxy:
    uuid: UUID
    stub: jumpstarter_pb2_grpc.ControllerServiceStub

    @asynccontextmanager
    async def channel(self):
        with TemporaryDirectory() as tempdir:
            socketpath = Path(tempdir) / "socket"

            async with await create_unix_listener(socketpath) as listener:
                async with create_task_group() as tg:
                    tg.start_soon(self._accept, listener)

                    async with grpc.aio.insecure_channel(
                        f"unix://{socketpath}"
                    ) as channel:
                        yield channel

    async def _accept(self, listener):
        response = await self.stub.Dial(
            jumpstarter_pb2.DialRequest(uuid=str(self.uuid))
        )

        async with await listener.accept() as stream:
            await connect_router_stream(
                response.router_endpoint, response.router_token, stream
            )
