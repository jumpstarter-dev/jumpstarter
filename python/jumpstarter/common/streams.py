from anyio import create_task_group, create_memory_object_stream, BrokenResourceError
from anyio.streams.stapled import StapledObjectStream
from jumpstarter.v1 import router_pb2


async def forward_server_stream(request_iterator, stream):
    async with create_task_group() as tg:

        async def client_to_server():
            try:
                async for frame in request_iterator:
                    await stream.send(frame.payload)
            except BrokenResourceError:
                pass
            finally:
                await stream.send_eof()

        tg.start_soon(client_to_server)

        # server_to_client
        try:
            async for payload in stream:
                yield router_pb2.StreamResponse(payload=payload)
        except BrokenResourceError:
            pass


def create_memory_stream():
    a_tx, a_rx = create_memory_object_stream[bytes](32)
    b_tx, b_rx = create_memory_object_stream[bytes](32)
    a = StapledObjectStream(a_tx, b_rx)
    b = StapledObjectStream(b_tx, a_rx)
    return a, b
