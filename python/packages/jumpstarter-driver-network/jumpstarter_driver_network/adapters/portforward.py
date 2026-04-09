from contextlib import asynccontextmanager
from functools import partial
from os import PathLike

from jumpstarter.client import DriverClient
from jumpstarter.client.adapters import blocking
from jumpstarter.common import TemporaryTcpListener, TemporaryUnixListener
from jumpstarter.streams.common import forward_stream


async def handler(client, method, conn):
    async with conn:
        async with client.stream_async(method) as stream:
            async with forward_stream(conn, stream):
                pass


@blocking
@asynccontextmanager
async def TcpPortforwardAdapter(
    *,
    client: DriverClient,
    method: str = "connect",
    local_host: str = "127.0.0.1",
    local_port: int = 0,
):
    async with TemporaryTcpListener(
        partial(handler, client, method),
        local_host=local_host,
        local_port=local_port,
    ) as addr:
        yield addr


@blocking
@asynccontextmanager
async def UnixPortforwardAdapter(
    *,
    client: DriverClient,
    method: str = "connect",
    path: PathLike | None = None,
):
    async with TemporaryUnixListener(partial(handler, client, method), path=path) as addr:
        yield addr
