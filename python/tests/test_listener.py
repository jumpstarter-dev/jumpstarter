import socket
from dataclasses import dataclass, field
from uuid import uuid4

import anyio
import grpc
import pytest
from anyio import Event
from anyio.abc import AnyByteStream
from anyio.from_thread import start_blocking_portal
from anyio.to_process import run_sync

from jumpstarter.client import LeaseRequest
from jumpstarter.client.lease import Lease
from jumpstarter.common import MetadataFilter
from jumpstarter.common.grpc import secure_channel
from jumpstarter.drivers.power.driver import MockPower
from jumpstarter.exporter import Exporter
from jumpstarter.streams import RouterStream, forward_stream
from jumpstarter.v1 import (
    jumpstarter_pb2_grpc,
    router_pb2_grpc,
)

pytestmark = pytest.mark.anyio


@dataclass(kw_only=True)
class MockRouter(router_pb2_grpc.RouterServiceServicer):
    pending: dict[str, AnyByteStream] = field(default_factory=dict)

    async def Stream(self, _request_iterator, context):
        event = Event()
        context.add_done_callback(lambda _: event.set())
        authorization = dict(list(context.invocation_metadata()))["authorization"]
        async with RouterStream(context=context) as stream:
            if authorization in self.pending:
                async with forward_stream(stream, self.pending[authorization]):
                    await event.wait()
            else:
                self.pending[authorization] = stream
                await event.wait()
                del self.pending[authorization]


def blocking(uuid):
    credentials = grpc.composite_channel_credentials(
        grpc.local_channel_credentials(),
        grpc.access_token_call_credentials(str(uuid)),
    )

    with start_blocking_portal() as portal:
        with LeaseRequest(
            channel=portal.call(secure_channel, "localhost:8083", credentials),
            metadata_filter=MetadataFilter(labels={"example.com/purpose": "test"}),
            portal=portal,
        ) as lease:
            with lease.connect() as client:
                assert client.on() == "ok"

            with lease.connect() as client:
                assert client.on() == "ok"


async def test_router():
    uuid = uuid4()

    router = MockRouter()
    server = grpc.aio.server()
    server.add_insecure_port("127.0.0.1:8083")

    router_pb2_grpc.add_RouterServiceServicer_to_server(router, server)

    await server.start()

    exporter = Exporter(
        channel=grpc.aio.insecure_channel("grpc.invalid"),
        uuid=uuid,
        labels={},
        device_factory=lambda: MockPower(),
    )

    async with exporter._Exporter__handle("127.0.0.1:8083", str(uuid)):
        with start_blocking_portal() as portal:
            lease = Lease(channel=grpc.aio.insecure_channel("grpc.invalid"), uuid=uuid, portal=portal)

            async with lease._Lease__connect("127.0.0.1:8083", str(uuid)) as client:
                assert await client.call_async("on") == "ok"

    await server.stop(grace=None)


@pytest.mark.skipif(
    socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect_ex(("localhost", 8082)) != 0,
    reason="controller not available",
)
async def test_listener():
    uuid = uuid4()

    credentials = grpc.composite_channel_credentials(
        grpc.local_channel_credentials(),
        grpc.access_token_call_credentials(str(uuid)),
    )

    channel = grpc.aio.secure_channel("localhost:8083", credentials)
    controller = jumpstarter_pb2_grpc.ControllerServiceStub(channel)

    async with Exporter(
        controller=controller,
        uuid=uuid,
        labels={"example.com/purpose": "test"},
        device_factory=lambda: MockPower(),
    ) as r:
        async with anyio.create_task_group() as tg:
            tg.start_soon(r.serve)

            await run_sync(blocking, uuid)

            tg.cancel_scope.cancel()
