from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import grpc

from jumpstarter.client import client_from_channel


@asynccontextmanager
async def serve(servicer):
    server = grpc.aio.server()
    servicer.add_to_server(server)

    with TemporaryDirectory() as tempdir:
        socketpath = Path(tempdir) / "socket"
        server.add_insecure_port(f"unix://{socketpath}")

        await server.start()

        async with grpc.aio.insecure_channel(f"unix://{socketpath}") as channel:
            yield await client_from_channel(channel)

        await server.stop(grace=None)
