from dataclasses import dataclass
from urllib.parse import urlencode, urlunparse

from jumpstarter.streams import WebsocketServerStream, forward_stream

from .portforward import PortforwardAdapter


@dataclass(kw_only=True)
class NovncAdapter(PortforwardAdapter):
    async def __aenter__(self):
        addr = await super().__aenter__()
        return urlunparse(
            (
                "https",
                "novnc.com",
                "/noVNC/vnc.html",
                "",
                urlencode({"autoconnect": 1, "reconnect": 1, "host": addr[0], "port": addr[1]}),
                "",
            )
        )

    async def handler(self, conn):
        async with conn:
            async with self.client.stream_async(self.method) as stream:
                async with WebsocketServerStream(stream=stream) as stream:
                    async with forward_stream(conn, stream):
                        pass
