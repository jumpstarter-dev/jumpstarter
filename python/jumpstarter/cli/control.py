import click
import os
import anyio
import grpc

from jumpstarter.client import client_from_channel


async def j_impl(host):
    channel = grpc.aio.insecure_channel(host)
    client = await client_from_channel(channel)
    return client.cli()


@click.command
def control():
    """Control DUT interactively"""
    raise click.UsageError("`jmp control` is only available within `jmp shell`")


def control_from_env():
    host = os.environ.get("JUMPSTARTER_HOST", None)
    if host is None:
        return control

    return anyio.from_thread.run(j_impl, host)
