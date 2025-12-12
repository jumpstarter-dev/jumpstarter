from contextlib import asynccontextmanager
from urllib.parse import urlencode, urlunparse

from ..streams import WebsocketServerStream
from jumpstarter.client import DriverClient
from jumpstarter.client.adapters import blocking
from jumpstarter.common import TemporaryTcpListener
from jumpstarter.streams.common import forward_stream


@blocking
@asynccontextmanager
async def NovncAdapter(*, client: DriverClient, method: str = "connect", encrypt: bool = False):
    """
    Provide a noVNC URL that proxies a temporary local TCP listener to a remote driver stream via a WebSocket bridge.
    
    Parameters:
        client (DriverClient): Client used to open the remote stream that will be bridged to the local listener.
        method (str): Name of the async stream method to call on the client (default "connect").
        encrypt (bool): If True use "https" in the generated URL; if False use "http" and include `encrypt=0` in the URL query.
    
    Returns:
        str: A fully constructed noVNC URL pointing at the temporary listener (host and port encoded in the query).
    """
    async def handler(conn):
        async with conn:
            async with client.stream_async(method) as stream:
                async with WebsocketServerStream(stream=stream) as stream:
                    async with forward_stream(conn, stream):
                        pass

    async with TemporaryTcpListener(handler) as addr:
        scheme = "https" if encrypt else "http"
        params = {
            "autoconnect": 1,
            "reconnect": 1,
            "host": addr[0],
            "port": addr[1],
        }
        if not encrypt:
            params["encrypt"] = 0

        yield urlunparse(
            (
                scheme,
                "novnc.com",
                "/noVNC/vnc.html",
                "",
                urlencode(params),
                "",
            )
        )