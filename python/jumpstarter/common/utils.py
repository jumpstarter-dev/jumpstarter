import os
import sys
from contextlib import asynccontextmanager, contextmanager

import anyio
import grpc
from anyio.from_thread import start_blocking_portal

from jumpstarter.client import client_from_channel
from jumpstarter.exporter import Session


@asynccontextmanager
async def serve_async(root_device, portal):
    with Session(root_device=root_device) as session:
        async with session.serve_unix_async() as path:
            async with grpc.aio.secure_channel(
                f"unix://{path}", grpc.local_channel_credentials(grpc.LocalConnectionType.UDS)
            ) as channel:
                yield await client_from_channel(channel, portal)


@contextmanager
def serve(root_device):
    with start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(serve_async(root_device, portal)) as client:
            yield client


@asynccontextmanager
async def env_async(portal):
    host = os.environ.get("JUMPSTARTER_HOST", None)
    if host is None:
        raise RuntimeError("JUMPSTARTER_HOST not set")

    async with grpc.aio.secure_channel(host, grpc.local_channel_credentials(grpc.LocalConnectionType.UDS)) as channel:
        yield await client_from_channel(channel, portal)


@contextmanager
def env():
    with start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(env_async(portal)) as client:
            yield client


async def launch_shell(host):
    async with await anyio.open_process(
        [os.environ.get("SHELL", "bash")],
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
        env=os.environ
        | {
            "JUMPSTARTER_HOST": host,
        },
    ) as process:
        await process.wait()
