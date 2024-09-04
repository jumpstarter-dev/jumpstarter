from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import grpc
from anyio import connect_unix, sleep_forever
from grpc.aio import Channel

from jumpstarter.common import Metadata
from jumpstarter.common.streams import connect_router_stream
from jumpstarter.driver import Driver
from jumpstarter.exporter.session import Session
from jumpstarter.v1 import (
    jumpstarter_pb2,
    jumpstarter_pb2_grpc,
)


@dataclass(kw_only=True)
class Exporter(AbstractAsyncContextManager, Metadata):
    channel: Channel
    device_factory: Callable[[], Driver]

    def __post_init__(self):
        super().__post_init__()
        jumpstarter_pb2_grpc.ControllerServiceStub.__init__(self, self.channel)

    async def __aenter__(self):
        probe = self.device_factory()

        await self.Register(
            jumpstarter_pb2.RegisterRequest(
                uuid=str(self.uuid),
                labels=self.labels,
                reports=(await probe.GetReport(None, None)).reports,
            )
        )

        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.Unregister(
            jumpstarter_pb2.UnregisterRequest(
                uuid=str(self.uuid),
                reason="TODO",
            )
        )

    @asynccontextmanager
    async def __handle(self, endpoint, token):
        root_device = self.device_factory()

        session = Session(
            uuid=self.uuid,
            labels=self.labels,
            root_device=root_device,
        )

        with TemporaryDirectory() as tempdir:
            socketpath = Path(tempdir) / "socket"

            server = grpc.aio.server()
            server.add_insecure_port(f"unix://{socketpath}")

            session.add_to_server(server)

            try:
                await server.start()

                async with await connect_unix(socketpath) as stream:
                    async with connect_router_stream(endpoint, token, stream):
                        yield
            finally:
                await server.stop(grace=None)

    async def serve(self):
        async for request in self.Listen(jumpstarter_pb2.ListenRequest()):
            async with self.__handle(self, request.router_endpoint, request.router_token):
                await sleep_forever()
