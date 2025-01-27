import os
import sys
from contextlib import asynccontextmanager, contextmanager
from subprocess import Popen

from anyio.from_thread import BlockingPortal, start_blocking_portal

from jumpstarter.client import client_from_path
from jumpstarter.config.client import _allow_from_env
from jumpstarter.config.env import JMP_DRIVERS_ALLOW, JUMPSTARTER_HOST
from jumpstarter.driver import Driver
from jumpstarter.exporter import Session


@asynccontextmanager
async def serve_async(root_device: Driver, portal: BlockingPortal):
    with Session(root_device=root_device) as session:
        async with session.serve_unix_async() as path:
            # SAFETY: the root_device instance is constructed locally thus considered trusted
            async with client_from_path(path, portal, allow=[], unsafe=True) as client:
                yield client
                if hasattr(client, "close"):
                    client.close()


@contextmanager
def serve(root_device: Driver):
    with start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(serve_async(root_device, portal)) as client:
            yield client
            if hasattr(client, "close"):
                client.close()


@asynccontextmanager
async def env_async(portal):
    """Provide a client for an existing JUMPSTARTER_HOST environment variable.

    Async version of env()

    This is useful when interacting with an already established Jumpstarter shell,
    to either a local exporter or a remote one.
    """
    host = os.environ.get(JUMPSTARTER_HOST, None)
    if host is None:
        raise RuntimeError(f"{JUMPSTARTER_HOST} not set")

    allow, unsafe = _allow_from_env()

    async with client_from_path(host, portal, allow=allow, unsafe=unsafe) as client:
        yield client
        if hasattr(client, "close"):
            client.close()


@contextmanager
def env():
    """Provide a client for an existing JUMPSTARTER_HOST environment variable.

    This is useful when interacting with an already established Jumpstarter shell,
    to either a local exporter or a remote one.
    """
    with start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(env_async(portal)) as client:
            yield client


def launch_shell(host: str, allow: list[str], unsafe: bool):
    process = Popen(
        [os.environ.get("SHELL", "bash")],
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
        env=os.environ
        | {
            JUMPSTARTER_HOST: host,
            JMP_DRIVERS_ALLOW: "UNSAFE" if unsafe else ",".join(allow),
        },
    )
    process.wait()
