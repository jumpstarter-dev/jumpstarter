import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field

from anyio import connect_unix, create_task_group, sleep
from grpc.aio import Channel

from jumpstarter.common import Metadata
from jumpstarter.common.streams import connect_router_stream
from jumpstarter.driver import Driver
from jumpstarter.exporter.session import Session
from jumpstarter.v1 import (
    jumpstarter_pb2,
    jumpstarter_pb2_grpc,
)

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class Exporter(AbstractAsyncContextManager, Metadata):
    channel: Channel
    device_factory: Callable[[], Driver]
    lease_name: str | None = field(init=False, default=None)

    def __post_init__(self):
        super().__post_init__()
        jumpstarter_pb2_grpc.ControllerServiceStub.__init__(self, self.channel)

    async def __aenter__(self):
        with Session(
            uuid=self.uuid,
            labels=self.labels,
            root_device=self.device_factory(),
        ) as probe:
            logger.info("Registering exporter with controller")
            await self.Register(
                jumpstarter_pb2.RegisterRequest(
                    labels=self.labels,
                    reports=(await probe.GetReport(None, None)).reports,
                )
            )

        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        logger.info("Unregistering exporter with controller")
        await self.Unregister(
            jumpstarter_pb2.UnregisterRequest(
                reason="TODO",
            )
        )

    @asynccontextmanager
    async def __handle(self, endpoint, token):
        root_device = self.device_factory()

        with Session(
            uuid=self.uuid,
            labels=self.labels,
            root_device=root_device,
        ) as session:
            async with session.serve_unix_async() as path:
                async with await connect_unix(path) as stream:
                    async with connect_router_stream(endpoint, token, stream):
                        yield

    async def handle(self):
        logger.info("Listening for incoming connection requests")
        async for request in self.Listen(jumpstarter_pb2.ListenRequest()):
            logger.info("Handling new connection request")
            async with self.__handle(request.router_endpoint, request.router_token):
                pass

    async def serve(self):
        async with create_task_group() as tg:
            tg.start_soon(self.handle)
            async for status in self.Status(jumpstarter_pb2.StatusRequest()):
                if self.lease_name is not None and self.lease_name != status.lease_name:
                    self.lease_name = status.lease_name
                    logger.info("Lease status changed, killing existing connections")
                    tg.cancel_scope.cancel()
                    break
                if status.leased:
                    logger.info("Currently leased by %s under %s", status.client_name, status.lease_name)
                else:
                    logger.info("Currently not leased")

    async def serve_forever(self):
        backoff = 5
        while True:
            try:
                await self.serve()
            except Exception as e:
                logger.info("Exporter: connection interrupted, reconnecting after %d seconds: %s", backoff, e)
                await sleep(backoff)
