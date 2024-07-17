from jumpstarter.v1 import (
    jumpstarter_pb2,
    jumpstarter_pb2_grpc,
)
from jumpstarter.common.streams import connect_router_stream
from jumpstarter.common import Metadata
from dataclasses import dataclass
from contextlib import AbstractAsyncContextManager
from tempfile import TemporaryDirectory
from pathlib import Path
from anyio import create_task_group, connect_unix
import grpc


@dataclass(kw_only=True)
class Registration(AbstractAsyncContextManager):
    controller: jumpstarter_pb2_grpc.ControllerServiceStub
    metadata: Metadata
    device_reports: list[jumpstarter_pb2.DeviceReport]

    async def __aenter__(self):
        await self.controller.Register(
            jumpstarter_pb2.RegisterRequest(
                uuid=str(self.metadata.uuid),
                labels=self.metadata.labels,
                device_report=self.device_reports,
            )
        )

        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.controller.Bye(
            jumpstarter_pb2.ByeRequest(
                uuid=str(self.metadata.uuid),
                reason="TODO",
            )
        )

    async def serve(self, exporter):
        with TemporaryDirectory() as tempdir:
            socketpath = Path(tempdir) / "socket"

            server = grpc.aio.server()
            server.add_insecure_port(f"unix://{socketpath}")

            exporter.add_to_server(server)

            try:
                await server.start()

                async with create_task_group() as tg:
                    async for request in self.controller.Listen(
                        jumpstarter_pb2.ListenRequest()
                    ):
                        tg.start_soon(self._handle, request, socketpath)
            finally:
                await server.stop(grace=None)

    async def _handle(self, request, socketpath):
        async with await connect_unix(socketpath) as stream:
            await connect_router_stream(
                request.router_endpoint, request.router_token, stream
            )
