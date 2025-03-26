import os
import sys
from contextlib import ExitStack, asynccontextmanager, contextmanager
from subprocess import Popen

from anyio.from_thread import BlockingPortal, start_blocking_portal

from jumpstarter.client import client_from_path
from jumpstarter.config.client import _allow_from_env
from jumpstarter.config.env import JMP_DRIVERS_ALLOW, JUMPSTARTER_HOST
from jumpstarter.driver import Driver
from jumpstarter.exporter import Session


@asynccontextmanager
async def serve_async(root_device: Driver, portal: BlockingPortal, stack: ExitStack):
    with Session(root_device=root_device) as session:
        async with session.serve_unix_async() as path:
            # SAFETY: the root_device instance is constructed locally thus considered trusted
            async with client_from_path(path, portal, stack, allow=[], unsafe=True) as client:
                try:
                    yield client
                finally:
                    if hasattr(client, "close"):
                        client.close()


@contextmanager
def serve(root_device: Driver):
    with start_blocking_portal() as portal:
        with ExitStack() as stack:
            with portal.wrap_async_context_manager(serve_async(root_device, portal, stack)) as client:
                try:
                    yield client
                finally:
                    if hasattr(client, "close"):
                        client.close()


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

    allow, unsafe = _allow_from_env()

    async with client_from_path(host, portal, stack, allow=allow, unsafe=unsafe) as client:
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


ANSI_GRAY = "\\[\\e[90m\\]"
ANSI_YELLOW = "\\[\\e[93m\\]"
ANSI_WHITE = "\\[\\e[97m\\]"
ANSI_RESET = "\\[\\e[0m\\]"
PROMPT_CWD = "\\W"


def launch_shell(
    host: str,
    context: str,
    allow: [str],
    unsafe: bool,
    *,
    command: tuple[str, ...] | None = None,
) -> int:
    """Launch a shell with a custom prompt indicating the exporter type.

    Args:
        host: The jumpstarter host path
        context: The context of the shell ("local" or "remote")
        allow: List of allowed drivers
        unsafe: Whether to allow drivers outside of the allow list
    """

    env = os.environ | {
        JUMPSTARTER_HOST: host,
        JMP_DRIVERS_ALLOW: "UNSAFE" if unsafe else ",".join(allow),
        "PS1": f"{ANSI_GRAY}{PROMPT_CWD} {ANSI_YELLOW}⚡{ANSI_WHITE}{context} {ANSI_YELLOW}➤{ANSI_RESET} ",
    }

    if command:
        process = Popen(command, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, env=env)
    else:
        cmd = [os.environ.get("SHELL", "bash")]
        if cmd[0].endswith("bash"):
            cmd.append("--norc")
            cmd.append("--noprofile")

        process = Popen(cmd, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, env=env)

    return process.wait()
