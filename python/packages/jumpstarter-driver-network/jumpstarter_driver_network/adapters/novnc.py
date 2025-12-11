from contextlib import asynccontextmanager
from urllib.parse import urlencode, urlunparse

from ..streams import WebsocketServerStream
from jumpstarter.client import DriverClient
from jumpstarter.client.adapters import blocking
from jumpstarter.common import TemporaryTcpListener
from jumpstarter.streams.common import forward_stream


@blocking
@asynccontextmanager
async def NovncAdapter(*, client: DriverClient, method: str = "connect", encrypt: bool = True):
    async def handler(conn):
        async with conn:
            async with client.stream_async(method) as stream:
                async with WebsocketServerStream(stream=stream) as stream:
                    async with forward_stream(conn, stream):
                        pass

    async with TemporaryTcpListener(handler) as addr:
        params = {
            "encrypt": 1 if encrypt else 0,
            "autoconnect": 1,
            "reconnect": 1,
            "host": addr[0],
            "port": addr[1],
        }

        yield urlunparse(
            (
                "https",
                "novnc.com",
                "/noVNC/vnc.html",
                "",
                urlencode(params),
                "",
            )
        )
