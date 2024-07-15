from jumpstarter.v1 import router_pb2
from anyio import create_task_group, BrokenResourceError


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
