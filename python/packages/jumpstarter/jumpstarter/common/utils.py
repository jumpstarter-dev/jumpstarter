import os
import sys
from contextlib import ExitStack, asynccontextmanager, contextmanager
from subprocess import Popen

from anyio.from_thread import BlockingPortal, start_blocking_portal

from jumpstarter.client import client_from_path
from jumpstarter.config.env import JMP_DRIVERS_ALLOW, JUMPSTARTER_HOST
from jumpstarter.driver import Driver
from jumpstarter.exporter import Session
from jumpstarter.utils.env import env

__all__ = ["env"]


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


ANSI_GRAY = "\\[\\e[90m\\]"
ANSI_YELLOW = "\\[\\e[93m\\]"
ANSI_WHITE = "\\[\\e[97m\\]"
ANSI_RESET = "\\[\\e[0m\\]"
PROMPT_CWD = "\\W"


def launch_shell(
    host: str,
    context: str,
    allow: list[str],
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

    shell = os.environ.get("SHELL", "bash")
    shell_name = os.path.basename(shell)

    common_env = os.environ | {
        JUMPSTARTER_HOST: host,
        JMP_DRIVERS_ALLOW: "UNSAFE" if unsafe else ",".join(allow),
    }

    if command:
        process = Popen(command, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, env=common_env)
        return process.wait()

    if shell_name.endswith("bash"):
        PS1=f"{ANSI_GRAY}{PROMPT_CWD} {ANSI_YELLOW}⚡{ANSI_WHITE}{context} {ANSI_YELLOW}➤{ANSI_RESET} "
        env = common_env | {
            'PROMPT_COMMAND': f'PS1="{PS1}"',
        }
        process = Popen(shell, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, env=env)
        return process.wait()

    elif shell_name == "fish":
        fish_fn = (
            "function fish_prompt; "
            "set_color grey; "
            'printf "%s" (basename $PWD); '
            "set_color yellow; "
            'printf "⚡"; '
            "set_color white; "
            f'printf "{context}"; '
            "set_color yellow; "
            'printf "➤ "; '
            "set_color normal; "
            "end"
        )
        cmd = [shell, "--init-command", fish_fn]
        process = Popen(cmd, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, env=common_env)
        return process.wait()

    elif shell_name == "zsh":
        env = common_env | {
            "PS1": f"%F{{8}}%1~ %F{{yellow}}⚡%F{{white}}{context} %F{{yellow}}➤%f ",
        }
        cmd = [shell, "--no-rcs"]
        process = Popen(cmd, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, env=env)
        return process.wait()

    else:
        process = Popen([shell], stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, env=common_env)
        return process.wait()
