import os

import grpc
from anyio.from_thread import start_blocking_portal
from anyio.to_thread import run_sync

from jumpstarter.client import client_from_channel


async def main_async(portal):
    host = os.environ.get("JUMPSTARTER_HOST", None)

    if host is None:
        raise RuntimeError("j command can only be used under jmp shell")

    async with grpc.aio.secure_channel(host, grpc.local_channel_credentials(grpc.LocalConnectionType.UDS)) as channel:
        client = await client_from_channel(channel, portal)

        def cli():
            client.cli()(standalone_mode=False)

        await run_sync(cli)


def main():
    with start_blocking_portal() as portal:
        portal.call(main_async, portal)


if __name__ == "__main__":
    main()
