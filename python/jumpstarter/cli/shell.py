import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import anyio
import click
import grpc

from jumpstarter.drivers.composite import Composite
from jumpstarter.drivers.network import EchoNetwork
from jumpstarter.drivers.power import MockPower
from jumpstarter.drivers.storage import MockStorageMux
from jumpstarter.exporter import Session


async def shell_impl():
    server = grpc.aio.server()

    session = Session(
        name="transient",
        root_device=Composite(
            name="root",
            children=[
                MockPower(name="power"),
                MockStorageMux(name="storage"),
                EchoNetwork(name="echo"),
            ],
        ),
    )
    session.add_to_server(server)

    with TemporaryDirectory() as tempdir:
        socketpath = Path(tempdir) / "socket"
        server.add_insecure_port(f"unix://{socketpath}")

        await server.start()

        async with await anyio.open_process(
            [os.environ["SHELL"]],
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
            env=os.environ
            | {
                "JUMPSTARTER_HOST": f"unix://{socketpath}",
            },
        ) as process:
            await process.wait()

        await server.stop(grace=None)


@click.command()
def shell():
    """Spawns a shell with a transient exporter session"""
    anyio.run(shell_impl)
