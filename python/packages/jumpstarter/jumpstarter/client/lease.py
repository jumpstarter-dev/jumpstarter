import logging
from contextlib import AbstractAsyncContextManager, AbstractContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass, field

from anyio import fail_after, sleep
from anyio.from_thread import BlockingPortal
from google.protobuf import duration_pb2
from grpc.aio import Channel
from jumpstarter_protocol import jumpstarter_pb2, jumpstarter_pb2_grpc, kubernetes_pb2

from .exceptions import LeaseError
from jumpstarter.client import client_from_path
from jumpstarter.common import MetadataFilter, TemporaryUnixListener
from jumpstarter.common.condition import condition_false, condition_message, condition_present_and_equal, condition_true
from jumpstarter.common.grpc import translate_grpc_exceptions
from jumpstarter.common.streams import connect_router_stream
from jumpstarter.config.tls import TLSConfigV1Alpha1

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class Lease(AbstractContextManager, AbstractAsyncContextManager):
    channel: Channel
    timeout: int = 1800
    metadata_filter: MetadataFilter = field(default_factory=MetadataFilter)
    portal: BlockingPortal
    name: str | None = field(default=None)
    allow: list[str]
    unsafe: bool
    release: bool = True  # release on contexts exit
    controller: jumpstarter_pb2_grpc.ControllerServiceStub = field(init=False)
    tls_config: TLSConfigV1Alpha1 = field(default_factory=TLSConfigV1Alpha1)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self.controller = jumpstarter_pb2_grpc.ControllerServiceStub(self.channel)
        self.manager = self.portal.wrap_async_context_manager(self)

    async def _create(self):
        duration = duration_pb2.Duration()
        duration.FromSeconds(self.timeout)
        duration_str = f"{duration.seconds}s"

        logger.debug("Creating lease request for labels %s for %s", self.metadata_filter.labels, duration_str)
        with translate_grpc_exceptions():
            self.name = (
                await self.controller.RequestLease(
                    jumpstarter_pb2.RequestLeaseRequest(
                        duration=duration,
                        selector=kubernetes_pb2.LabelSelector(match_labels=self.metadata_filter.labels),
                    )
                )
            ).name
        logger.info("Created lease request for labels %s for %s", self.metadata_filter.labels, duration_str)

    def request(self):
        """Request a lease, or verifies a lease which was already created.

        :return: lease
        :rtype: Lease
        :raises LeaseError: if lease is unsatisfiable
        :raises LeaseError: if lease is not pending
        :raises TimeoutError: if lease is not ready after timeout
        """
        return self.portal.call(self.request_async)

    async def request_async(self):
        """Request a lease, or verifies a lease which was already created.

        :return: lease
        :rtype: Lease
        :raises LeaseError: if lease is unsatisfiable
        :raises LeaseError: if lease is not pending
        :raises TimeoutError: if lease is not ready after timeout
        """
        if self.name:
            logger.debug("Using existing lease %s", self.name)
        else:
            await self._create()
        return await self._acquire()

    async def _acquire(self):
        """Acquire a lease.

        Makes sure the lease is ready, and returns the lease object.
        """
        with fail_after(300):  # TODO: configurable timeout
            while True:
                logger.debug("Polling Lease %s", self.name)
                with translate_grpc_exceptions():
                    result = await self.controller.GetLease(jumpstarter_pb2.GetLeaseRequest(name=self.name))

                # lease ready
                if condition_true(result.conditions, "Ready"):
                    logger.debug("Lease %s acquired", self.name)
                    return self
                # lease unsatisfiable
                if condition_true(result.conditions, "Unsatisfiable"):
                    message = condition_message(result.conditions, "Unsatisfiable")
                    logger.debug(
                        "Lease %s cannot be satisfied: %s",
                        self.name,
                        condition_message(result.conditions, "Unsatisfiable"),
                    )
                    raise LeaseError(f"the lease cannot be satisfied: {message}")

                # lease not pending
                if condition_false(result.conditions, "Pending"):
                    raise LeaseError(
                        f"Lease {self.name} is not in pending, but it isn't in Ready or Unsatisfiable state either"
                    )

                # lease released
                if condition_present_and_equal(result.conditions, "Ready", "False", "Released"):
                    raise LeaseError(f"lease {self.name} released")

                await sleep(1)

    async def __aenter__(self):
        return await self.request_async()

    async def __aexit__(self, exc_type, exc_value, traceback):
        if self.release:
            logger.info("Releasing Lease %s", self.name)
            await self.controller.ReleaseLease(jumpstarter_pb2.ReleaseLeaseRequest(name=self.name))

    def __enter__(self):
        # wraps the async context manager enter
        return self.manager.__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        # wraps the async context manager exit
        return self.manager.__exit__(exc_type, exc_value, traceback)

    async def handle_async(self, stream):
        logger.debug("Connecting to Lease with name %s", self.name)
        response = await self.controller.Dial(jumpstarter_pb2.DialRequest(lease_name=self.name))
        async with connect_router_stream(response.router_endpoint, response.router_token, stream, self.tls_config):
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
