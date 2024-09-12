from contextlib import asynccontextmanager, contextmanager

import grpc
from anyio.from_thread import start_blocking_portal

from jumpstarter.client import client_from_channel
from jumpstarter.exporter import Session


@asynccontextmanager
async def serve_async(root_device, portal):
    session = Session(root_device=root_device)
    async with session.serve_unix_async() as path:
        async with grpc.aio.insecure_channel(f"unix://{path}") as channel:
            yield await client_from_channel(channel, portal)


@contextmanager
def serve(root_device):
    with start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(serve_async(root_device, portal)) as client:
            yield client
