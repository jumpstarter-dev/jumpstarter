from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

import grpc
from anyio import create_task_group, create_unix_listener
from google.protobuf import duration_pb2

from jumpstarter.common import MetadataFilter
from jumpstarter.common.streams import connect_router_stream
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc


@dataclass(kw_only=True)
class LeaseRequest(AbstractAsyncContextManager):
    controller: jumpstarter_pb2_grpc.ControllerServiceStub
    metadata_filter: MetadataFilter

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
                return Lease(controller=self.controller, uuid=exporter.uuid)
            case "failure":
                raise RuntimeError(result.failure.reason)

    async def __aexit__(self, exc_type, exc_value, traceback):
        # TODO: release exporter
        pass


@dataclass(kw_only=True)
class Lease:
    controller: jumpstarter_pb2_grpc.ControllerServiceStub
    uuid: UUID

    @asynccontextmanager
    async def connect(self):
        response = await self.controller.Dial(jumpstarter_pb2.DialRequest(uuid=str(self.uuid)))

        with TemporaryDirectory() as tempdir:
            socketpath = Path(tempdir) / "socket"

            async with await create_unix_listener(socketpath) as listener:
                async with create_task_group() as tg:
                    tg.start_soon(self._accept, listener, response)

                    async with grpc.aio.insecure_channel(f"unix://{socketpath}") as inner:
                        yield inner

    async def _accept(self, listener, response):
        async with await listener.accept() as stream:
            await connect_router_stream(response.router_endpoint, response.router_token, stream)
