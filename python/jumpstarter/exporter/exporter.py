from jumpstarter.v1 import (
    jumpstarter_pb2,
    jumpstarter_pb2_grpc,
)
from jumpstarter.exporter import Session
from jumpstarter.common.streams import connect_router_stream
from jumpstarter.common import Metadata
from jumpstarter.drivers import ContextStore, Store, Driver
from collections.abc import Callable
from dataclasses import dataclass
from contextlib import AbstractAsyncContextManager
from tempfile import TemporaryDirectory
from pathlib import Path
from anyio import connect_unix
import grpc


@dataclass(kw_only=True)
class Exporter(AbstractAsyncContextManager, Metadata):
    controller: jumpstarter_pb2_grpc.ControllerServiceStub
    device_factory: Callable[[], Driver]

    async def __aenter__(self):
        probe = self.device_factory()

        await self.controller.Register(
            jumpstarter_pb2.RegisterRequest(
                uuid=str(self.uuid),
                labels=self.labels,
                reports=probe.reports(),
            )
        )

        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.controller.Unregister(
            jumpstarter_pb2.UnregisterRequest(
                uuid=str(self.uuid),
                reason="TODO",
            )
        )

    async def serve(self):
        async for request in self.controller.Listen(jumpstarter_pb2.ListenRequest()):
            root_device = self.device_factory()

            session = Session(
                uuid=self.uuid,
                labels=self.labels,
                root_device=root_device,
            )

            with TemporaryDirectory() as tempdir:
                socketpath = Path(tempdir) / "socket"

                ContextStore.set(Store())

                server = grpc.aio.server()
                server.add_insecure_port(f"unix://{socketpath}")

                session.add_to_server(server)

                try:
                    await server.start()

                    async with await connect_unix(socketpath) as stream:
                        await connect_router_stream(
                            request.router_endpoint, request.router_token, stream
                        )
                finally:
                    await server.stop(grace=None)
