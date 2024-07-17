from jumpstarter.common.streams import connect_router_stream
from jumpstarter.common import MetadataFilter
from google.protobuf import duration_pb2
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc
from dataclasses import dataclass, field
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from tempfile import TemporaryDirectory
from pathlib import Path
from anyio import create_unix_listener, create_task_group
from uuid import UUID
import grpc


@dataclass(kw_only=True)
class Lease(AbstractAsyncContextManager):
    controller: jumpstarter_pb2_grpc.ControllerServiceStub
    metadata_filter: MetadataFilter
    uuid: UUID | None = field(default=None, init=False)

    async def __aenter__(self):
        exporters = (
            await self.controller.ListExporters(
                jumpstarter_pb2.ListExportersRequest(
                    labels=self.metadata_filter.labels,
                )
            )
        ).exporters

        if not exporters:
            # TODO: retry/wait on transient unavailability
            raise FileNotFoundError("no matching exporters")

        exporter = exporters[0]

        duration = duration_pb2.Duration()
        duration.FromSeconds(1800)

        result = await self.controller.LeaseExporter(
            jumpstarter_pb2.LeaseExporterRequest(
                uuid=exporter.uuid,
                duration=duration,  # TODO: configurable duration
            )
        )

        match result.WhichOneof("lease_exporter_response_oneof"):
            case "success":
                self.uuid = exporter.uuid
                return self
            case "failure":
                raise RuntimeError(result.failure.reason)

    async def __aexit__(self, exc_type, exc_value, traceback):
        # TODO: release exporter
        pass

    @asynccontextmanager
    async def connect(self):
        if self.uuid is None:
            raise ValueError("exporter not leased")

        response = await self.controller.Dial(
            jumpstarter_pb2.DialRequest(uuid=str(self.uuid))
        )

        with TemporaryDirectory() as tempdir:
            socketpath = Path(tempdir) / "socket"

            async with await create_unix_listener(socketpath) as listener:
                async with create_task_group() as tg:
                    tg.start_soon(self._accept, listener, response)

                    async with grpc.aio.insecure_channel(
                        f"unix://{socketpath}"
                    ) as inner:
                        yield inner

    async def _accept(self, listener, response):
        async with await listener.accept() as stream:
            await connect_router_stream(
                response.router_endpoint, response.router_token, stream
            )
