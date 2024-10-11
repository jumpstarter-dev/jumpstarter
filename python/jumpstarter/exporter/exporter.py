import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field

import grpc
from anyio import connect_unix, create_task_group, sleep
from google.protobuf import empty_pb2

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
    channel: grpc.aio.Channel
    device_factory: Callable[[], Driver]
    controller: jumpstarter_pb2_grpc.ControllerServiceStub = field(init=False)
    lease_name: str | None = field(init=False, default=None)

    def __post_init__(self):
        super().__post_init__()
        self.controller = jumpstarter_pb2_grpc.ControllerServiceStub(self.channel)

    async def __aexit__(self, exc_type, exc_value, traceback):
        logger.info("Unregistering exporter with controller")
        await self.controller.Unregister(
            jumpstarter_pb2.UnregisterRequest(
                reason="TODO",
            )
        )

    async def __handle(self, path, endpoint, token):
        async with await connect_unix(path) as stream:
            async with connect_router_stream(endpoint, token, stream):
                pass

    async def handle(self, lease_name, tg):
        logger.info("Listening for incoming connection requests on lease %s", lease_name)
        with Session(
            uuid=self.uuid,
            labels=self.labels,
            root_device=self.device_factory(),
        ) as session:
            async with session.serve_unix_async() as path:
                async with grpc.aio.secure_channel(
                    f"unix://{path}", grpc.local_channel_credentials(grpc.LocalConnectionType.UDS)
                ) as channel:
                    response = await jumpstarter_pb2_grpc.ExporterServiceStub(channel).GetReport(empty_pb2.Empty())
                    await self.controller.Register(
                        jumpstarter_pb2.RegisterRequest(
                            labels=self.labels,
                            reports=response.reports,
                        )
                    )
                async for request in self.controller.Listen(jumpstarter_pb2.ListenRequest(lease_name=lease_name)):
                    logger.info("Handling new connection request on lease %s", lease_name)
                    tg.start_soon(self.__handle, path, request.router_endpoint, request.router_token)

    async def serve(self):
        started = False
        async with create_task_group() as tg:
            async for status in self.controller.Status(jumpstarter_pb2.StatusRequest()):
                if self.lease_name is not None and self.lease_name != status.lease_name:
                    self.lease_name = status.lease_name
                    logger.info("Lease status changed, killing existing connections")
                    tg.cancel_scope.cancel()
                    break
                self.lease_name = status.lease_name
                if not started and self.lease_name is not None:
                    started = True
                    tg.start_soon(self.handle, self.lease_name, tg)
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
