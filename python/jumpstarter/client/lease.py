from contextlib import AbstractContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from anyio import create_unix_listener
from anyio.from_thread import BlockingPortal
from google.protobuf import duration_pb2
from grpc.aio import Channel

from jumpstarter.client import client_from_channel
from jumpstarter.common import MetadataFilter
from jumpstarter.common.grpc import insecure_channel
from jumpstarter.common.streams import connect_router_stream
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc


@dataclass(kw_only=True)
class LeaseRequest(AbstractContextManager, jumpstarter_pb2_grpc.ControllerServiceStub):
    channel: Channel
    metadata_filter: MetadataFilter
    portal: BlockingPortal

    def __post_init__(self):
        super().__post_init__()
        jumpstarter_pb2_grpc.ControllerServiceStub.__init__(self, self.channel)

    def __enter__(self):
        exporters = self.portal.call(
            self.ListExporters,
            jumpstarter_pb2.ListExportersRequest(
                labels=self.metadata_filter.labels,
            ),
        ).exporters

        if not exporters:
            # TODO: retry/wait on transient unavailability
            raise FileNotFoundError("no matching exporters")

        exporter = exporters[0]

        duration = duration_pb2.Duration()
        duration.FromSeconds(1800)

        result = self.portal.call(
            self.LeaseExporter,
            jumpstarter_pb2.LeaseExporterRequest(
                uuid=exporter.uuid,
                duration=duration,  # TODO: configurable duration
            ),
        )

        match result.WhichOneof("lease_exporter_response_oneof"):
            case "success":
                return Lease(channel=self.channel, uuid=exporter.uuid, portal=self.portal)
            case "failure":
                raise RuntimeError(result.failure.reason)

    def __exit__(self, exc_type, exc_value, traceback):
        # TODO: release exporter
        pass


@dataclass(kw_only=True)
class Lease:
    channel: Channel
    uuid: UUID
    portal: BlockingPortal

    def __post_init__(self):
        super().__post_init__()
        jumpstarter_pb2_grpc.ControllerServiceStub.__init__(self, self.channel)

    @asynccontextmanager
    async def connect_async(self):
        response = await self.Dial(jumpstarter_pb2.DialRequest(uuid=str(self.uuid)))

        with TemporaryDirectory() as tempdir:
            socketpath = Path(tempdir) / "socket"
            async with await create_unix_listener(socketpath) as listener:
                async with await insecure_channel(f"unix://{socketpath}") as inner:
                    inner.get_state(try_to_connect=True)
                    async with await listener.accept() as stream:
                        async with connect_router_stream(response.router_endpoint, response.router_token, stream):
                            yield await client_from_channel(inner, self.portal)

    @contextmanager
    def connect(self):
        with self.portal.wrap_async_context_manager(self.connect_async()) as client:
            yield client
