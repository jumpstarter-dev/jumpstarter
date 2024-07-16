from jumpstarter.common.streams import connect_router_stream
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc
from contextlib import asynccontextmanager
from tempfile import TemporaryDirectory
from pathlib import Path
from anyio import create_unix_listener, create_task_group
from uuid import UUID
import grpc


class Proxy:
    @classmethod
    @asynccontextmanager
    async def connect(
        cls, stub: jumpstarter_pb2_grpc.ControllerServiceStub, uuid: UUID
    ):
        response = await stub.Dial(jumpstarter_pb2.DialRequest(uuid=str(uuid)))

        with TemporaryDirectory() as tempdir:
            socketpath = Path(tempdir) / "socket"

            async with await create_unix_listener(socketpath) as listener:
                async with create_task_group() as tg:
                    tg.start_soon(cls._accept, listener, response)

                    async with grpc.aio.insecure_channel(
                        f"unix://{socketpath}"
                    ) as inner:
                        yield inner

    @classmethod
    async def _accept(cls, listener, response):
        async with await listener.accept() as stream:
            await connect_router_stream(
                response.router_endpoint, response.router_token, stream
            )
