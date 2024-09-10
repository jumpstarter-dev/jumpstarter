import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import anyio
import click
from anyio import create_task_group, create_unix_listener
from anyio.from_thread import start_blocking_portal

from jumpstarter.common import MetadataFilter
from jumpstarter.common.streams import connect_router_stream
from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter.config.exporter import ExporterConfigV1Alpha1
from jumpstarter.config.user import UserConfigV1Alpha1
from jumpstarter.v1 import jumpstarter_pb2


async def user_shell(host):
    async with await anyio.open_process(
        [os.environ["SHELL"]],
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
        env=os.environ
        | {
            "JUMPSTARTER_HOST": host,
        },
    ) as process:
        await process.wait()


async def client_shell(name):
    if name:
        client = ClientConfigV1Alpha1.load(name)
    else:
        client = UserConfigV1Alpha1.load_or_create().config.current_client

    if not client:
        raise ValueError("no client specified")

    with start_blocking_portal() as portal:
        async with client.lease_async(metadata_filter=MetadataFilter(), portal=portal) as lease:
            with TemporaryDirectory() as tempdir:
                socketpath = Path(tempdir) / "socket"
                async with await create_unix_listener(socketpath) as listener:
                    async with create_task_group() as tg:

                        async def handler(stream):
                            async with stream:
                                response = await lease.Dial(jumpstarter_pb2.DialRequest(uuid=str(lease.uuid)))
                                async with connect_router_stream(
                                    response.router_endpoint, response.router_token, stream
                                ):
                                    pass

                        tg.start_soon(listener.serve, handler)
                        await user_shell(f"unix://{socketpath}")
                        tg.cancel_scope.cancel()


async def exporter_shell(name):
    try:
        exporter = ExporterConfigV1Alpha1.load(name)
    except FileNotFoundError as e:
        raise click.ClickException(f"exporter config with name {name} not found: {e}") from e

    with TemporaryDirectory() as tempdir:
        socketpath = Path(tempdir) / "socket"
        async with exporter.serve_local(f"unix://{socketpath}"):
            await user_shell(f"unix://{socketpath}")


@click.command()
@click.option("--exporter")
@click.option("--client")
def shell(exporter, client):
    """Spawns a shell with a transient exporter session"""
    if exporter and client:
        raise ValueError("exporter and client cannot be both set")
    elif exporter:
        anyio.run(exporter_shell, exporter)
    else:
        anyio.run(client_shell, client)
