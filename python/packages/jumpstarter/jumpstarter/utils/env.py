import os
from contextlib import ExitStack, asynccontextmanager, contextmanager

from anyio.from_thread import start_blocking_portal

from jumpstarter.client import client_from_path
from jumpstarter.common.exceptions import EnvironmentVariableNotSetError
from jumpstarter.config.env import JMP_DRIVERS_ALLOW, JUMPSTARTER_HOST


def _drivers_from_env() -> tuple[list[str], bool]:
    """The allowed driver-client packages from ``JMP_DRIVERS_ALLOW`` (comma-separated). The
    sentinel ``UNSAFE`` enables loading any package. Plain stdlib — the parent shell sets these
    env vars; no pydantic config model."""
    allow_str = os.environ.get(JMP_DRIVERS_ALLOW, "")
    allow = allow_str.split(",") if allow_str else []
    unsafe = "UNSAFE" in allow
    return allow, unsafe


@asynccontextmanager
async def env_async(portal, stack):
    """Provide a client for an existing JUMPSTARTER_HOST environment variable.

    Async version of env()

    This is useful when interacting with an already established Jumpstarter shell,
    to either a local exporter or a remote one.
    """
    host = os.environ.get(JUMPSTARTER_HOST, None)
    if host is None:
        raise EnvironmentVariableNotSetError(f"{JUMPSTARTER_HOST} not set")

    allow, unsafe = _drivers_from_env()

    async with client_from_path(
        host,
        portal,
        stack,
        allow=allow,
        unsafe=unsafe,
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
