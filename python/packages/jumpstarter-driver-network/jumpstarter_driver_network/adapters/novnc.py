from contextlib import asynccontextmanager
from urllib.parse import urlencode, urlunparse

from ..streams import WebsocketServerStream
from jumpstarter.client import DriverClient
from jumpstarter.client.adapters import blocking
from jumpstarter.common import TemporaryTcpListener
from jumpstarter.streams.common import forward_stream


@blocking
@asynccontextmanager
async def NovncAdapter(*, client: DriverClient, method: str = "connect"):
    async def handler(conn):
        async with conn:
            async with client.stream_async(method) as stream:
                async with WebsocketServerStream(stream=stream) as stream:
                    async with forward_stream(conn, stream):
                        pass

    async with TemporaryTcpListener(handler) as addr:
        yield urlunparse(
            (
                "https",
                "novnc.com",
                "/noVNC/vnc.html",
                "",
                urlencode({"autoconnect": 1, "reconnect": 1, "host": addr[0], "port": addr[1]}),
                "",
            )
        )
