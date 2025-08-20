import logging
from collections.abc import AsyncGenerator, Generator
from contextlib import (
    ExitStack,
    asynccontextmanager,
    contextmanager,
)
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Self

from anyio import AsyncContextManagerMixin, ContextManagerMixin, create_task_group, fail_after, sleep
from anyio.from_thread import BlockingPortal
from grpc.aio import Channel
from jumpstarter_protocol import jumpstarter_pb2, jumpstarter_pb2_grpc

from .exceptions import LeaseError
from jumpstarter.client import client_from_path
from jumpstarter.client.grpc import ClientService
from jumpstarter.common import TemporaryUnixListener
from jumpstarter.common.condition import condition_false, condition_message, condition_present_and_equal, condition_true
from jumpstarter.common.grpc import translate_grpc_exceptions
from jumpstarter.common.streams import connect_router_stream
from jumpstarter.config.tls import TLSConfigV1Alpha1

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class Lease(ContextManagerMixin, AsyncContextManagerMixin):
    channel: Channel
    duration: timedelta
    selector: str
    portal: BlockingPortal
    namespace: str
    name: str | None = field(default=None)
    allow: list[str]
    unsafe: bool
    release: bool = True  # release on contexts exit
    controller: jumpstarter_pb2_grpc.ControllerServiceStub = field(init=False)
    tls_config: TLSConfigV1Alpha1 = field(default_factory=TLSConfigV1Alpha1)
    grpc_options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self.controller = jumpstarter_pb2_grpc.ControllerServiceStub(self.channel)
        self.svc = ClientService(channel=self.channel, namespace=self.namespace)

    async def _create(self):
        logger.debug("Creating lease request for selector %s for duration %s", self.selector, self.duration)
        with translate_grpc_exceptions():
            self.name = (
                await self.svc.CreateLease(
                    selector=self.selector,
                    duration=self.duration,
                )
            ).name
        logger.info("Created lease request for selector %s for duration %s", self.selector, self.duration)

    async def get(self):
        with translate_grpc_exceptions():
            svc = ClientService(channel=self.channel, namespace=self.namespace)
            return await svc.GetLease(name=self.name)

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
                result = await self.get()
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

    @asynccontextmanager
    async def __asynccontextmanager__(self) -> AsyncGenerator[Self]:
        yield await self.request_async()
        if self.release:
            logger.info("Releasing Lease %s", self.name)
            await self.svc.DeleteLease(
                name=self.name,
            )

    @contextmanager
    def __contextmanager__(self) -> Generator[Self]:
        with self.portal.wrap_async_context_manager(self) as value:
            yield value

    async def handle_async(self, stream):
        logger.debug("Connecting to Lease with name %s", self.name)
        response = await self.controller.Dial(jumpstarter_pb2.DialRequest(lease_name=self.name))
        async with connect_router_stream(
            response.router_endpoint, response.router_token, stream, self.tls_config, self.grpc_options
        ):
            pass

    @asynccontextmanager
    async def serve_unix_async(self):
        async with TemporaryUnixListener(self.handle_async) as path:
            yield path

    @asynccontextmanager
    async def monitor_async(self, threshold: timedelta = timedelta(minutes=5)):
        async def _monitor():
            while True:
                lease = await self.get()
                # TODO: use effective_end_time as the authoritative source for lease end time
                if lease.effective_begin_time:
                    end_time = lease.effective_begin_time + lease.duration
                    remain = end_time - datetime.now(tz=datetime.now().astimezone().tzinfo)
                    if remain < timedelta(0):
                        # lease already expired, stopping monitor
                        logger.info("Lease {} ended at {}".format(self.name, end_time))
                        break
                    elif remain < threshold:
                        # lease expiring soon, check again on expected expiration time in case it's extended
                        logger.info("Lease {} ending soon in {} at {}".format(self.name, remain, end_time))
                        await sleep(threshold.total_seconds())
                    else:
                        # lease still active, check again in 5 seconds
                        await sleep(5)
                else:
                    await sleep(1)

        async with create_task_group() as tg:
            tg.start_soon(_monitor)
            try:
                yield
            finally:
                tg.cancel_scope.cancel()

    @asynccontextmanager
    async def connect_async(self, stack):
        async with self.serve_unix_async() as path:
            async with client_from_path(path, self.portal, stack, allow=self.allow, unsafe=self.unsafe) as client:
                yield client

    @contextmanager
    def connect(self):
        with ExitStack() as stack:
            with self.portal.wrap_async_context_manager(self.connect_async(stack)) as client:
                yield client

    @contextmanager
    def serve_unix(self):
        with self.portal.wrap_async_context_manager(self.serve_unix_async()) as path:
            yield path

    @contextmanager
    def monitor(self, threshold: timedelta = timedelta(minutes=5)):
        with self.portal.wrap_async_context_manager(self.monitor_async(threshold)):
            yield
