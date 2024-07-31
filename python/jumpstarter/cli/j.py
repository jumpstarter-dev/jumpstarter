import os

from anyio.from_thread import start_blocking_portal

from jumpstarter.client import client_from_channel
from jumpstarter.common.grpc import insecure_channel


def main():
    host = os.environ.get("JUMPSTARTER_HOST", None)

    if host is None:
        raise RuntimeError("j command can only be used under jmp shell")

    with start_blocking_portal() as portal:
        channel = portal.call(insecure_channel, host)
        client = portal.call(client_from_channel, channel, portal)
        client.cli()()


if __name__ == "__main__":
    main()
