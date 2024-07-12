from . import Network
import anyio


class LocalNetwork(Network):
    async def connect(self):
        return await anyio.connect_tcp("127.0.0.1", 8880)
