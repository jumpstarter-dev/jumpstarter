import os
from contextlib import ExitStack, asynccontextmanager, contextmanager

from anyio.from_thread import start_blocking_portal

from jumpstarter.client import client_from_path
from jumpstarter.config.client import ClientConfigV1Alpha1Drivers
from jumpstarter.config.env import JUMPSTARTER_HOST


@asynccontextmanager
async def env_async(portal, stack):
    """Provide a client for an existing JUMPSTARTER_HOST environment variable.

    Async version of env()

    This is useful when interacting with an already established Jumpstarter shell,
    to either a local exporter or a remote one.
    """
    host = os.environ.get(JUMPSTARTER_HOST, None)
    if host is None:
        raise RuntimeError(f"{JUMPSTARTER_HOST} not set")

    drivers = ClientConfigV1Alpha1Drivers()

    async with client_from_path(
        host,
        portal,
        stack,
        allow=drivers.allow,
        unsafe=drivers.unsafe,
    ) as client:
        try:
            yield client
        finally:
            if hasattr(client, "close"):
                client.close()


@contextmanager
def env():
    """Provide a client for an existing JUMPSTARTER_HOST environment variable.

    This is useful when interacting with an already established Jumpstarter shell,
    to either a local exporter or a remote one.
    """
    with start_blocking_portal() as portal:
        with ExitStack() as stack:
            with portal.wrap_async_context_manager(env_async(portal, stack)) as client:
                yield client
