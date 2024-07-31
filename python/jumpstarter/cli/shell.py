import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import anyio
import click
import grpc

from jumpstarter.drivers.dutlink.base import Dutlink
from jumpstarter.exporter import Session


async def shell_impl():
    server = grpc.aio.server()

    session = Session(
        name="transient",
        root_device=Dutlink(
            name="root",
            storage_device="/dev/null",
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
