import grpc
from anyio import BrokenResourceError, ClosedResourceError, create_memory_object_stream, create_task_group
from anyio.streams.stapled import StapledObjectStream

from jumpstarter.v1 import router_pb2, router_pb2_grpc


async def encapsulate_stream(rx, cls):
    try:
        yield cls(frame_type=router_pb2.FRAME_TYPE_PING)
        async for payload in rx:
            yield cls(payload=payload)
        yield cls(frame_type=router_pb2.FRAME_TYPE_GOAWAY)
    except (BrokenResourceError, ClosedResourceError):
        pass


async def decapsulate_stream(tx, rx):
    try:
        async for frame in rx:
            match frame.frame_type:
                case router_pb2.FRAME_TYPE_DATA:
                    await tx.send(frame.payload)
                case router_pb2.FRAME_TYPE_PING:
                    pass
                case router_pb2.FRAME_TYPE_GOAWAY:
                    break
                case _:
                    pass
    except (BrokenResourceError, ClosedResourceError):
        pass


async def forward_server_stream(request_iterator, stream):
    async with create_task_group() as tg:
        tg.start_soon(decapsulate_stream, stream, request_iterator)

        async for v in encapsulate_stream(stream, router_pb2.StreamResponse):
            yield v


async def forward_client_stream(router, stream, metadata):
    try:
        response_iterator = router.Stream(
            encapsulate_stream(stream, router_pb2.StreamRequest),
            metadata=metadata,
        )
        await decapsulate_stream(stream, response_iterator)
        async for _ in response_iterator:
            pass
    except grpc.aio.AioRpcError:
        # TODO: handle connection error
        pass
    finally:
        await stream.aclose()


async def connect_router_stream(endpoint, token, stream):
    credentials = grpc.composite_channel_credentials(
        grpc.local_channel_credentials(),  # TODO: Use TLS
        grpc.access_token_call_credentials(token),
    )

    async with grpc.aio.secure_channel(endpoint, credentials) as channel:
        router = router_pb2_grpc.RouterServiceStub(channel)
        await forward_client_stream(router, stream, ())


def create_memory_stream():
    a_tx, a_rx = create_memory_object_stream[bytes](32)
    b_tx, b_rx = create_memory_object_stream[bytes](32)
    a = StapledObjectStream(a_tx, b_rx)
    b = StapledObjectStream(b_tx, a_rx)
    return a, b
