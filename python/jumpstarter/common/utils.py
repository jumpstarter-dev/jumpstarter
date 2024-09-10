from contextlib import asynccontextmanager, contextmanager

import grpc
from anyio.from_thread import start_blocking_portal

from jumpstarter.client import client_from_channel
from jumpstarter.exporter import Session

from .tempfile import TemporarySocket


@asynccontextmanager
async def serve_async(root_device, portal):
    server = grpc.aio.server()

    session = Session(root_device=root_device)
    session.add_to_server(server)

    with TemporarySocket() as path:
        server.add_insecure_port(f"unix://{path}")

        await server.start()

        async with grpc.aio.insecure_channel(f"unix://{path}") as channel:
            yield await client_from_channel(channel, portal)

        await server.stop(grace=None)


@contextmanager
def serve(root_device):
    with start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(serve_async(root_device, portal)) as client:
            yield client
