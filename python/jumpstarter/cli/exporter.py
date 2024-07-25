from uuid import uuid4

import anyio
import click
import grpc

from jumpstarter.drivers.power import MockPower
from jumpstarter.exporter import Exporter
from jumpstarter.v1 import jumpstarter_pb2_grpc


async def exporter_impl():
    uuid = uuid4()

    credentials = grpc.composite_channel_credentials(
        grpc.local_channel_credentials(),
        grpc.access_token_call_credentials(str(uuid)),
    )

    async with grpc.aio.secure_channel("localhost:8083", credentials) as channel:
        controller = jumpstarter_pb2_grpc.ControllerServiceStub(channel)
        async with Exporter(
            controller=controller,
            uuid=uuid,
            name="exporter",
            device_factory=lambda: MockPower(name="power"),
        ) as e:
            click.echo(f"Exporter {uuid} started")
            await e.serve()


@click.command
def exporter():
    anyio.run(exporter_impl)
