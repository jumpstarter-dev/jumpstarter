import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field

import grpc
from anyio import connect_unix, create_memory_object_stream, create_task_group, sleep
from google.protobuf import empty_pb2
from jumpstarter_protocol import (
    jumpstarter_pb2,
    jumpstarter_pb2_grpc,
)

from jumpstarter.common import Metadata
from jumpstarter.common.streams import connect_router_stream
from jumpstarter.config.tls import TLSConfigV1Alpha1
from jumpstarter.driver import Driver
from jumpstarter.exporter.session import Session

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class Exporter(AbstractAsyncContextManager, Metadata):
    channel_factory: Callable[[], grpc.aio.Channel]
    device_factory: Callable[[], Driver]
    lease_name: str = field(init=False, default="")
    tls: TLSConfigV1Alpha1 = field(default_factory=TLSConfigV1Alpha1)
    grpc_options: dict[str, str] = field(default_factory=dict)

    async def __aexit__(self, exc_type, exc_value, traceback):
        controller = jumpstarter_pb2_grpc.ControllerServiceStub(await self.channel_factory())
        logger.info("Unregistering exporter with controller")
        await controller.Unregister(
            jumpstarter_pb2.UnregisterRequest(
                reason="TODO",
            )
        )

    async def __handle(self, path, endpoint, token, tls_config, grpc_options):
        try:
            async with await connect_unix(path) as stream:
                async with connect_router_stream(endpoint, token, stream, tls_config, grpc_options):
                    pass
        except Exception as e:
            logger.info("failed to handle connection: {}".format(e))

    @asynccontextmanager
    async def session(self):
        controller = jumpstarter_pb2_grpc.ControllerServiceStub(await self.channel_factory())
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
                    logger.info("Registering exporter with controller")
                    await controller.Register(
                        jumpstarter_pb2.RegisterRequest(
                            labels=self.labels,
                            reports=response.reports,
                        )
                    )
                yield path

    async def handle(self, lease_name, tg):
        logger.info("Listening for incoming connection requests on lease %s", lease_name)

        listen_tx, listen_rx = create_memory_object_stream()

        async def listen(retries=5, backoff=3):
            retries_left = retries
            while True:
                try:
                    controller = jumpstarter_pb2_grpc.ControllerServiceStub(await self.channel_factory())
                    async for request in controller.Listen(jumpstarter_pb2.ListenRequest(lease_name=lease_name)):
                        await listen_tx.send(request)
                except Exception as e:
                    if retries_left > 0:
                        retries_left -= 1
                        logger.info(
                            "Listen stream interrupted, restarting in {}s, {} retries left: {}".format(
                                backoff, retries_left, e
                            )
                        )
                        await sleep(backoff)
                    else:
                        raise
                else:
                    retries_left = retries

        tg.start_soon(listen)

        async with self.session() as path:
            async for request in listen_rx:
                logger.info("Handling new connection request on lease %s", lease_name)
                tg.start_soon(
                    self.__handle, path, request.router_endpoint, request.router_token, self.tls, self.grpc_options
                )

    async def serve(self):  # noqa: C901
        # initial registration
        async with self.session():
            pass
        started = False
        status_tx, status_rx = create_memory_object_stream()

        async def status(retries=5, backoff=3):
            retries_left = retries
            while True:
                try:
                    controller = jumpstarter_pb2_grpc.ControllerServiceStub(await self.channel_factory())
                    async for status in controller.Status(jumpstarter_pb2.StatusRequest()):
                        await status_tx.send(status)
                except Exception as e:
                    if retries_left > 0:
                        retries_left -= 1
                        logger.info(
                            "Status stream interrupted, restarting in {}s, {} retries left: {}".format(
                                backoff, retries_left, e
                            )
                        )
                        await sleep(backoff)
                    else:
                        raise
                else:
                    retries_left = retries

        async with create_task_group() as tg:
            tg.start_soon(status)
            async for status in status_rx:
                if self.lease_name != "" and self.lease_name != status.lease_name:
                    self.lease_name = status.lease_name
                    logger.info("Lease status changed, killing existing connections")
                    tg.cancel_scope.cancel()
                    break
                self.lease_name = status.lease_name
                if not started and self.lease_name != "":
                    started = True
                    tg.start_soon(self.handle, self.lease_name, tg)
                if status.leased:
                    logger.info("Currently leased by %s under %s", status.client_name, status.lease_name)
                else:
                    logger.info("Currently not leased")
