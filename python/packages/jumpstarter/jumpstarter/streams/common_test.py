import errno
import socket
from contextlib import asynccontextmanager

import grpc
import pytest
from anyio import EndOfStream, Event, create_task_group, fail_after, sleep_forever
from anyio.abc import SocketStream
from anyio.streams.stapled import StapledObjectStream
from jumpstarter_protocol import router_pb2, router_pb2_grpc

from jumpstarter.streams.common import create_memory_stream, forward_stream
from jumpstarter.streams.router import RouterStream

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@asynccontextmanager
async def socket_pair():
    left, right = socket.socketpair()
    async with await SocketStream.from_socket(left) as local, await SocketStream.from_socket(right) as remote:
        yield local, remote


class DroppingRouter(router_pb2_grpc.RouterServiceServicer):
    async def Stream(self, requests, context):
        async for request in requests:
            if request.payload == b"disconnect":
                await context.abort(grpc.StatusCode.UNAVAILABLE, "injected outer transport loss")
            if request.frame_type == router_pb2.FRAME_TYPE_GOAWAY:
                break
            yield router_pb2.StreamResponse(payload=request.payload)


@pytest.fixture
async def dropping_router():
    server = grpc.aio.server()
    router_pb2_grpc.add_RouterServiceServicer_to_server(DroppingRouter(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            yield router_pb2_grpc.RouterServiceStub(channel)
    finally:
        await server.stop(grace=None)


@pytest.mark.parametrize("metrics_driver_type", [None, "test"])
@pytest.mark.parametrize("wait_in_context", [False, True])
async def test_router_failure_closes_nested_socket_and_allows_new_stream(
    dropping_router, metrics_driver_type, wait_in_context
):
    with fail_after(5):
        for attempt in range(3):
            finished = Event()
            async with socket_pair() as (client, relay), create_task_group() as tasks:

                async def forward(finished):
                    async with forward_stream(
                        relay, RouterStream(context=dropping_router.Stream()), metrics_driver_type=metrics_driver_type
                    ):
                        if wait_in_context:
                            await sleep_forever()
                    finished.set()

                tasks.start_soon(forward, finished)
                payload = f"request {attempt}".encode()
                await client.send(payload)
                assert await client.receive() == payload
                await client.send(b"disconnect")
                with pytest.raises(EndOfStream):
                    await client.receive()
                await finished.wait()

        async with socket_pair() as (client, relay), create_task_group() as tasks:

            async def forward():
                async with forward_stream(relay, RouterStream(context=dropping_router.Stream())):
                    pass

            tasks.start_soon(forward)
            await client.send(b"recovered")
            assert await client.receive() == b"recovered"
            await client.send_eof()
            with pytest.raises(EndOfStream):
                await client.receive()


async def test_failed_write_closes_other_direction_while_its_reader_is_idle():
    client, incoming = create_memory_stream()
    outgoing, server = create_memory_stream()
    with fail_after(5):
        async with client, server, create_task_group() as tasks:

            async def forward():
                async with forward_stream(incoming, outgoing):
                    pass

            # Fail writes while the reverse reader remains idle.
            await server.receive_stream.aclose()
            tasks.start_soon(forward)
            await client.send(b"cannot be delivered")
            with pytest.raises(EndOfStream):
                await client.receive()


async def test_forward_stream_preserves_response_after_request_half_close():
    with fail_after(5):
        async with (
            socket_pair() as (client, incoming),
            socket_pair() as (outgoing, server),
            create_task_group() as tasks,
        ):

            async def forward():
                async with forward_stream(incoming, outgoing):
                    pass

            tasks.start_soon(forward)
            await client.send(b"request")
            await client.send_eof()
            assert await server.receive() == b"request"
            with pytest.raises(EndOfStream):
                await server.receive()
            await server.send(b"response after EOF")
            await server.send_eof()
            assert await client.receive() == b"response after EOF"
            with pytest.raises(EndOfStream):
                await client.receive()


async def test_parent_cancellation_closes_both_idle_peers():
    finished = Event()
    with fail_after(5):
        async with socket_pair() as (client, incoming), socket_pair() as (outgoing, server):
            async with create_task_group() as tasks:

                async def forward(*, task_status):
                    try:
                        async with forward_stream(incoming, outgoing):
                            task_status.started()
                            await sleep_forever()
                    finally:
                        finished.set()

                await tasks.start(forward)
                tasks.cancel_scope.cancel()
            assert finished.is_set()
            for peer in (client, server):
                with pytest.raises(EndOfStream):
                    await peer.receive()


class _EofFails(StapledObjectStream):
    """A destination whose half-close raises ``error``."""

    def __init__(self, stream, error):
        super().__init__(stream.send_stream, stream.receive_stream)
        self.error = error

    async def send_eof(self):
        raise self.error


async def _forward_with_failing_eof(error, exchange):
    client, incoming = create_memory_stream()
    outgoing, server = create_memory_stream()
    with fail_after(5):
        async with client, server, create_task_group() as tasks:

            async def forward():
                async with forward_stream(incoming, _EofFails(outgoing, error)):
                    pass

            tasks.start_soon(forward)
            await client.send(b"request")
            await client.send_eof()
            assert await server.receive() == b"request"
            await exchange(client, server)


async def test_failed_eof_write_closes_other_direction():
    async def exchange(client, server):
        # The reverse reader is idle, so only the failed EOF can end the stream.
        with pytest.raises(EndOfStream):
            await client.receive()

    await _forward_with_failing_eof(OSError(errno.EPIPE, "Broken pipe"), exchange)


async def test_not_connected_eof_still_allows_response():
    # https://github.com/jumpstarter-dev/jumpstarter/issues/444: Darwin reports
    # ENOTCONN for a normal half-close, and the response must still flow.
    async def exchange(client, server):
        await server.send(b"response")
        assert await client.receive() == b"response"
        await server.send_eof()
        with pytest.raises(EndOfStream):
            await client.receive()

    await _forward_with_failing_eof(OSError(errno.ENOTCONN, "Socket is not connected"), exchange)
