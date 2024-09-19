
import anyio
import click
from anyio import create_task_group, create_unix_listener
from anyio.from_thread import start_blocking_portal

from jumpstarter.common import MetadataFilter, TemporarySocket
from jumpstarter.common.utils import launch_shell
from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter.config.exporter import ExporterConfigV1Alpha1
from jumpstarter.config.user import UserConfigV1Alpha1


async def client_shell(name, labels):
    if name:
        client = ClientConfigV1Alpha1.load(name)
    else:
        client = UserConfigV1Alpha1.load_or_create().config.current_client

    if not client:
        raise ValueError("no client specified")

    with start_blocking_portal() as portal:
        async with client.lease_async(metadata_filter=MetadataFilter(labels=labels), portal=portal) as lease:
            with TemporarySocket() as path:
                async with await create_unix_listener(path) as listener:
                    async with create_task_group() as tg:

                        async def handler(stream):
                            async with lease.handle_async(stream):
                                pass

                        tg.start_soon(listener.serve, handler)
                        await launch_shell(f"unix://{path}")
                        tg.cancel_scope.cancel()


async def exporter_shell(name):
    try:
        exporter = ExporterConfigV1Alpha1.load(name)
    except FileNotFoundError as e:
        raise click.ClickException(f"exporter config with name {name} not found: {e}") from e

    async with exporter.serve_unix_async() as path:
        await launch_shell(f"unix://{path}")


@click.group()
def shell():
    """Spawns a shell connecting to an exporter"""
    pass


@shell.command
@click.argument("name")
def exporter(name):
    """Spawns a shell connecting to a transient local exporter"""
    anyio.run(exporter_shell, name)


@shell.command
@click.argument("name")
@click.option("-l", "--label", "labels", type=(str, str), multiple=True)
def client(name, labels):
    """Spawns a shell connecting to a leased remote exporter"""
    anyio.run(client_shell, name, dict(labels))
