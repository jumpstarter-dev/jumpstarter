from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import grpc
from anyio.from_thread import start_blocking_portal

from jumpstarter.client import client_from_channel
from jumpstarter.exporter import Session


async def _create_grpc_server():
    return grpc.aio.server()


async def _create_grpc_channel(address):
    return grpc.aio.insecure_channel(address)


@contextmanager
def serve(root_device):
    with start_blocking_portal() as portal:
        server = portal.call(_create_grpc_server)

        session = Session(name="session", root_device=root_device)
        session.add_to_server(server)

        with TemporaryDirectory() as tempdir:
            socketpath = Path(tempdir) / "socket"
            server.add_insecure_port(f"unix://{socketpath}")

            portal.call(server.start)

            with portal.wrap_async_context_manager(
                portal.call(_create_grpc_channel, f"unix://{socketpath}")
            ) as channel:
                yield portal.call(client_from_channel, channel, portal)

            portal.call(server.stop, None)
