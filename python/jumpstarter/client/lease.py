import logging
from contextlib import AbstractAsyncContextManager, AbstractContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass, field

from anyio import fail_after, sleep
from anyio.from_thread import BlockingPortal
from google.protobuf import duration_pb2
from grpc.aio import Channel

from jumpstarter.client import client_from_path
from jumpstarter.common import MetadataFilter, TemporaryUnixListener
from jumpstarter.common.condition import condition_false, condition_true
from jumpstarter.common.streams import connect_router_stream
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc, kubernetes_pb2

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class Lease(AbstractContextManager, AbstractAsyncContextManager):
    channel: Channel
    timeout: int = 1800
    metadata_filter: MetadataFilter = field(default_factory=MetadataFilter)
    portal: BlockingPortal
    lease_name: str | None = field(default=None)
    allow: list[str]
    unsafe: bool
    controller: jumpstarter_pb2_grpc.ControllerServiceStub = field(init=False)

    def __post_init__(self):
        self.controller = jumpstarter_pb2_grpc.ControllerServiceStub(self.channel)
        self.manager = self.portal.wrap_async_context_manager(self)

    async def __aenter__(self):
        if self.lease_name:
            logger.info("Using existing lease %s", self.lease_name)
        else:
            duration = duration_pb2.Duration()
            duration.FromSeconds(self.timeout)

            logger.info("Leasing Exporter matching labels %s for %s", self.metadata_filter.labels, duration)
            self.lease_name = (
                await self.controller.RequestLease(
                    jumpstarter_pb2.RequestLeaseRequest(
                        duration=duration,
                        selector=kubernetes_pb2.LabelSelector(match_labels=self.metadata_filter.labels),
                    )
                )
            ).name
            logger.info("Lease %s created", self.lease_name)

        with fail_after(300):  # TODO: configurable timeout
            while True:
                logger.info("Polling Lease %s", self.lease_name)
                result = await self.controller.GetLease(jumpstarter_pb2.GetLeaseRequest(name=self.lease_name))

                # lease ready
                if condition_true(result.conditions, "Ready"):
                    logger.info("Lease %s acquired", self.lease_name)
                    return self
                # lease unsatisfiable
                if condition_true(result.conditions, "Unsatisfiable"):
                    raise ValueError("lease unsatisfiable")
                # lease not pending
                if condition_false(result.conditions, "Pending"):
                    raise ValueError("lease not pending")

                await sleep(1)

    async def __aexit__(self, exc_type, exc_value, traceback):
        logger.info("Releasing Lease %s", self.lease_name)
        await self.controller.ReleaseLease(jumpstarter_pb2.ReleaseLeaseRequest(name=self.lease_name))

    def __enter__(self):
        return self.manager.__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        return self.manager.__exit__(exc_type, exc_value, traceback)

    async def handle_async(self, stream):
        logger.info("Connecting to Lease with name %s", self.lease_name)
        response = await self.controller.Dial(jumpstarter_pb2.DialRequest(lease_name=self.lease_name))
        async with connect_router_stream(response.router_endpoint, response.router_token, stream):
            pass

    @asynccontextmanager
    async def serve_unix_async(self):
        async with TemporaryUnixListener(self.handle_async) as path:
            yield path

    @asynccontextmanager
    async def connect_async(self):
        async with self.serve_unix_async() as path:
            async with client_from_path(path, self.portal, allow=self.allow, unsafe=self.unsafe) as client:
                yield client

    @contextmanager
    def connect(self):
        with self.portal.wrap_async_context_manager(self.connect_async()) as client:
            yield client

    @contextmanager
    def serve_unix(self):
        with self.portal.wrap_async_context_manager(self.serve_unix_async()) as path:
            yield path
