import logging
from contextlib import AbstractAsyncContextManager, AbstractContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from uuid import UUID

import grpc
from anyio import create_unix_listener, fail_after, sleep
from anyio.from_thread import BlockingPortal
from google.protobuf import duration_pb2
from grpc.aio import Channel

from jumpstarter.client import client_from_channel
from jumpstarter.common import MetadataFilter, TemporarySocket
from jumpstarter.common.condition import condition_false, condition_true
from jumpstarter.common.streams import connect_router_stream
from jumpstarter.streams import CancelTask
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc, kubernetes_pb2

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class LeaseRequest(AbstractContextManager, AbstractAsyncContextManager):
    channel: Channel
    metadata_filter: MetadataFilter
    portal: BlockingPortal
    stub: jumpstarter_pb2_grpc.ControllerServiceStub = field(init=False)

    def __post_init__(self):
        self.stub = jumpstarter_pb2_grpc.ControllerServiceStub(self.channel)
        self.manager = self.portal.wrap_async_context_manager(self)

    async def __aenter__(self):
        duration = duration_pb2.Duration()
        duration.FromSeconds(1800)  # TODO: configurable duration

        logger.info("Leasing Exporter matching labels %s for %s", self.metadata_filter.labels, duration)
        self.lease = await self.stub.RequestLease(
            jumpstarter_pb2.RequestLeaseRequest(
                duration=duration,
                selector=kubernetes_pb2.LabelSelector(match_labels=self.metadata_filter.labels),
            )
        )
        logger.info("Lease %s created", self.lease.name)
        with fail_after(300):  # TODO: configurable timeout
            while True:
                logger.info("Polling Lease %s", self.lease.name)
                result = await self.stub.GetLease(jumpstarter_pb2.GetLeaseRequest(name=self.lease.name))

                # lease ready
                if condition_true(result.conditions, "Ready"):
                    logger.info("Lease %s acquired", self.lease.name)
                    return Lease(channel=self.channel, uuid=UUID(result.exporter_uuid), portal=self.portal)
                # lease unsatisfiable
                if condition_true(result.conditions, "Unsatisfiable"):
                    raise ValueError("lease unsatisfiable")
                # lease not pending
                if condition_false(result.conditions, "Pending"):
                    raise ValueError("lease not pending")
                await sleep(1)

    async def __aexit__(self, exc_type, exc_value, traceback):
        logger.info("Releasing Lease %s", self.lease.name)
        await self.stub.ReleaseLease(jumpstarter_pb2.ReleaseLeaseRequest(name=self.lease.name))

    def __enter__(self):
        return self.manager.__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        return self.manager.__exit__(exc_type, exc_value, traceback)


@dataclass(kw_only=True)
class Lease:
    channel: Channel
    uuid: UUID
    portal: BlockingPortal
    stub: jumpstarter_pb2_grpc.ControllerServiceStub = field(init=False)

    def __post_init__(self):
        self.stub = jumpstarter_pb2_grpc.ControllerServiceStub(self.channel)

    @asynccontextmanager
    async def handle_async(self, stream):
        logger.info("Connecting to Exporter with uuid %s", self.uuid)
        response = await self.stub.Dial(jumpstarter_pb2.DialRequest(uuid=str(self.uuid)))
        async with connect_router_stream(response.router_endpoint, response.router_token, stream):
            yield

    @asynccontextmanager
    async def connect_async(self):
        with TemporarySocket() as path:
            async with await create_unix_listener(path) as listener:
                async with grpc.aio.secure_channel(
                    f"unix://{path}", grpc.local_channel_credentials(grpc.LocalConnectionType.UDS)
                ) as channel:
                    channel.get_state(try_to_connect=True)
                    async with await listener.accept() as stream:
                        async with self.handle_async(stream):
                            yield await client_from_channel(channel, self.portal)
                            raise CancelTask

    @contextmanager
    def connect(self):
        with self.portal.wrap_async_context_manager(self.connect_async()) as client:
            yield client
