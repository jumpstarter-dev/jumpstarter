# These tests are flaky
# https://github.com/grpc/grpc/issues/25364

from dataclasses import dataclass, field
from uuid import uuid4

import grpc
import pytest
from anyio import Event, create_memory_object_stream, create_task_group
from anyio.abc import AnyByteStream
from anyio.from_thread import start_blocking_portal
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from jumpstarter.client import LeaseRequest
from jumpstarter.client.lease import Lease
from jumpstarter.common import MetadataFilter
from jumpstarter.drivers.power.driver import MockPower
from jumpstarter.exporter import Exporter
from jumpstarter.streams import RouterStream, forward_stream
from jumpstarter.v1 import (
    jumpstarter_pb2,
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


@dataclass(kw_only=True)
class MockController(jumpstarter_pb2_grpc.ControllerServiceServicer):
    router_endpoint: str
    queue: (MemoryObjectSendStream[str], MemoryObjectReceiveStream[str]) = field(
        init=False, default_factory=lambda: create_memory_object_stream[str](32)
    )

    async def Register(self, request, context):
        return jumpstarter_pb2.RegisterResponse(uuid=str(uuid4()))

    async def Unregister(self, request, context):
        return jumpstarter_pb2.UnregisterResponse()

    async def RequestLease(self, request, context):
        return jumpstarter_pb2.RequestLeaseResponse(name=str(uuid4()))

    async def GetLease(self, request, context):
        return jumpstarter_pb2.GetLeaseResponse(exporter_uuid=str(uuid4()))

    async def ReleaseLease(self, request, context):
        return jumpstarter_pb2.ReleaseLeaseResponse()

    async def Dial(self, request, context):
        token = str(uuid4())
        await self.queue[0].send(token)
        return jumpstarter_pb2.DialResponse(router_endpoint=self.router_endpoint, router_token=token)

    async def Listen(self, request, context):
        async for token in self.queue[1]:
            yield jumpstarter_pb2.ListenResponse(router_endpoint=self.router_endpoint, router_token=token)


@pytest.mark.xfail(raises=RuntimeError)
async def test_router():
    uuid = uuid4()

    router = MockRouter()
    server = grpc.aio.server()
    port = server.add_insecure_port("127.0.0.1:0")

    router_pb2_grpc.add_RouterServiceServicer_to_server(router, server)

    await server.start()

    exporter = Exporter(
        channel=grpc.aio.insecure_channel("grpc.invalid"),
        uuid=uuid,
        labels={},
        device_factory=lambda: MockPower(),
    )

    async with exporter._Exporter__handle(f"127.0.0.1:{port}", str(uuid)):
        with start_blocking_portal() as portal:
            lease = Lease(channel=grpc.aio.insecure_channel("grpc.invalid"), uuid=uuid, portal=portal)

            async with lease._Lease__connect(f"127.0.0.1:{port}", str(uuid)) as client:
                assert await client.call_async("on") == "ok"

    await server.stop(grace=None)


@pytest.mark.xfail(raises=RuntimeError)
async def test_controller():
    server = grpc.aio.server()
    port = server.add_insecure_port("127.0.0.1:0")

    controller = MockController(router_endpoint=f"127.0.0.1:{port}")
    router = MockRouter()

    jumpstarter_pb2_grpc.add_ControllerServiceServicer_to_server(controller, server)
    router_pb2_grpc.add_RouterServiceServicer_to_server(router, server)

    await server.start()

    uuid = uuid4()

    async with Exporter(
        channel=grpc.aio.insecure_channel(f"127.0.0.1:{port}"),
        uuid=uuid,
        labels={},
        device_factory=lambda: MockPower(),
    ) as exporter:
        async with create_task_group() as tg:
            tg.start_soon(exporter.serve)

            with start_blocking_portal() as portal:
                async with LeaseRequest(
                    channel=grpc.aio.insecure_channel(f"127.0.0.1:{port}"),
                    metadata_filter=MetadataFilter(),
                    portal=portal,
                ) as lease:
                    async with lease.connect_async() as client:
                        assert await client.call_async("on") == "ok"

                    async with lease.connect_async() as client:
                        assert await client.call_async("on") == "ok"

            tg.cancel_scope.cancel()

    await server.stop(grace=None)
