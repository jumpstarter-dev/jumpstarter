import logging
from collections import deque
from contextlib import AbstractContextManager, asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass, field
from logging.handlers import QueueHandler
from uuid import UUID

import grpc
from anyio import Event, TypedAttributeLookupError, sleep
from anyio.from_thread import start_blocking_portal
from jumpstarter_protocol import (
    jumpstarter_pb2,
    jumpstarter_pb2_grpc,
    router_pb2_grpc,
)

from .logging import LogHandler
from jumpstarter.common import Metadata, TemporarySocket
from jumpstarter.common.streams import StreamRequestMetadata
from jumpstarter.driver import Driver
from jumpstarter.streams.common import forward_stream
from jumpstarter.streams.metadata import MetadataStreamAttributes
from jumpstarter.streams.router import RouterStream

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class Session(
    jumpstarter_pb2_grpc.ExporterServiceServicer,
    router_pb2_grpc.RouterServiceServicer,
    Metadata,
    AbstractContextManager,
):
    root_device: Driver
    mapping: dict[UUID, Driver]

    _logging_queue: deque = field(init=False)
    _logging_handler: QueueHandler = field(init=False)

    def __enter__(self):
        logging.getLogger().addHandler(self._logging_handler)
        self.root_device.reset()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.root_device.close()
        logging.getLogger().removeHandler(self._logging_handler)

    def __init__(self, *args, root_device, **kwargs):
        super().__init__(*args, **kwargs)

        self.root_device = root_device
        self.mapping = {u: i for (u, _, _, i) in self.root_device.enumerate()}

        self._logging_queue = deque(maxlen=32)
        self._logging_handler = LogHandler(self._logging_queue)

    @asynccontextmanager
    async def serve_port_async(self, port):
        server = grpc.aio.server()
        server.add_insecure_port(port)

        jumpstarter_pb2_grpc.add_ExporterServiceServicer_to_server(self, server)
        router_pb2_grpc.add_RouterServiceServicer_to_server(self, server)

        await server.start()
        try:
            yield
        finally:
            await server.stop(grace=None)

    @asynccontextmanager
    async def serve_unix_async(self):
        with TemporarySocket() as path:
            async with self.serve_port_async(f"unix://{path}"):
                yield path

    @contextmanager
    def serve_unix(self):
        with start_blocking_portal() as portal:
            with portal.wrap_async_context_manager(self.serve_unix_async()) as path:
                yield path

    def __getitem__(self, key: UUID):
        return self.mapping[key]

    async def GetReport(self, request, context):
        logger.debug("GetReport()")
        return jumpstarter_pb2.GetReportResponse(
            uuid=str(self.uuid),
            labels=self.labels,
            reports=[
                instance.report(parent=parent, name=name)
                for (_, parent, name, instance) in self.root_device.enumerate()
            ],
        )

    async def DriverCall(self, request, context):
        logger.debug("DriverCall(uuid=%s, method=%s)", request.uuid, request.method)
        return await self[UUID(request.uuid)].DriverCall(request, context)

    async def StreamingDriverCall(self, request, context):
        logger.debug("StreamingDriverCall(uuid=%s, method=%s)", request.uuid, request.method)
        async for v in self[UUID(request.uuid)].StreamingDriverCall(request, context):
            yield v

    async def Stream(self, _request_iterator, context):
        request = StreamRequestMetadata(**dict(list(context.invocation_metadata()))).request
        logger.debug("Streaming(%s)", request)
        async with self[request.uuid].Stream(request, context) as stream:
            metadata = []
            with suppress(TypedAttributeLookupError):
                metadata.extend(stream.extra(MetadataStreamAttributes.metadata).items())
            await context.send_initial_metadata(metadata)

            async with RouterStream(context=context) as remote:
                async with forward_stream(remote, stream):
                    event = Event()
                    context.add_done_callback(lambda _: event.set())
                    await event.wait()

    async def LogStream(self, request, context):
        while True:
            try:
                yield self._logging_queue.popleft()
            except IndexError:
                await sleep(0.5)
