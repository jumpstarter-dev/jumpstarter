import os
import signal
import sys
import tempfile
from contextlib import ExitStack, asynccontextmanager, contextmanager
from datetime import timedelta
from functools import partial
from subprocess import Popen
from typing import TYPE_CHECKING

from anyio.from_thread import BlockingPortal, start_blocking_portal

from jumpstarter.client import client_from_path
from jumpstarter.config.env import JMP_DRIVERS_ALLOW, JMP_GRPC_INSECURE, JMP_GRPC_PASSPHRASE, JUMPSTARTER_HOST
from jumpstarter.exporter import Session
from jumpstarter.utils.env import env

if TYPE_CHECKING:
    from jumpstarter.driver import Driver

__all__ = ["env"]


@asynccontextmanager
async def serve_async(root_device: "Driver", portal: BlockingPortal, stack: ExitStack):
    from jumpstarter.common import ExporterStatus

    with Session(root_device=root_device) as session:
        async with session.serve_unix_async() as path:
            # For local testing, set status to LEASE_READY since there's no lease/hook flow
            session.update_status(ExporterStatus.LEASE_READY)
            # SAFETY: the root_device instance is constructed locally thus considered trusted
            async with client_from_path(path, portal, stack, allow=[], unsafe=True) as client:
                try:
                    yield client
                finally:
                    if hasattr(client, "close"):
                        client.close()


@contextmanager
def serve(root_device: "Driver"):
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


def lease_ending_handler(process: Popen, lease, remaining_time) -> None:
    """Lease ending handler to terminate a process when lease ends.

    Args:
        process: The process to terminate
        lease: The lease instance
        remaining_time: Time remaining until lease expiration
    """

    if remaining_time <= timedelta(0):
        try:
            process.send_signal(signal.SIGHUP)
        except (ProcessLookupError, OSError):
            pass  # Process already terminated


def _run_process(
    cmd: list[str],
    env: dict[str, str],
    lease=None,
) -> int:
    """Helper to run a process with an option to set a lease ending callback."""
    process = Popen(cmd, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, env=env)
    if lease is not None:
        lease.lease_ending_callback = partial(lease_ending_handler, process)
    return process.wait()


def _generate_shell_init(shell_name: str, use_profiles: bool, j_commands: list[str] | None = None) -> str:
    if shell_name.endswith("bash"):
        lines = []
        if use_profiles:
            lines.append('[ -f ~/.bashrc ] && source ~/.bashrc')
        lines.append('eval "$(jmp completion bash 2>/dev/null)"')
        lines.append('eval "$(jmp-admin completion bash 2>/dev/null)"')
        if j_commands:
            cmds = " ".join(j_commands)
            lines.append(
                f'_j_completion() {{ COMPREPLY=($(compgen -W "{cmds}" -- "${{COMP_WORDS[COMP_CWORD]}}")); }}'
            )
            lines.append("complete -o default -F _j_completion j")
        else:
            lines.append('eval "$(j completion bash 2>/dev/null)"')
        return "\n".join(lines) + "\n"

    elif shell_name == "zsh":
        lines = []
        if use_profiles:
            lines.append('[ -f ~/.zshrc ] && source ~/.zshrc')
        lines.append('eval "$(jmp completion zsh 2>/dev/null)"')
        lines.append('eval "$(jmp-admin completion zsh 2>/dev/null)"')
        if j_commands:
            cmds = " ".join(j_commands)
            lines.append(f"compdef '_arguments \"1:(({cmds}))\"' j")
        else:
            lines.append('eval "$(j completion zsh 2>/dev/null)"')
        return "\n".join(lines) + "\n"

    elif shell_name == "fish":
        lines = []
        lines.append("jmp completion fish 2>/dev/null | source")
        lines.append("jmp-admin completion fish 2>/dev/null | source")
        if j_commands:
            for cmd in j_commands:
                lines.append(f"complete -c j -f -n '__fish_use_subcommand' -a {cmd}")
        else:
            lines.append("j completion fish 2>/dev/null | source")
        return "\n".join(lines) + "\n"

    return ""


def launch_shell(  # noqa: C901
    host: str,
    context: str,
    allow: list[str],
    unsafe: bool,
    use_profiles: bool,
    *,
    command: tuple[str, ...] | None = None,
    lease=None,
    insecure: bool = False,
    passphrase: str | None = None,
    j_commands: list[str] | None = None,
) -> int:
    """Launch a shell with a custom prompt indicating the exporter type."""

    shell = os.environ.get("SHELL", "bash")
    shell_name = os.path.basename(shell)
    common_env = os.environ | {
        JUMPSTARTER_HOST: host,
        JMP_DRIVERS_ALLOW: "UNSAFE" if unsafe else ",".join(allow),
        "_JMP_SUPPRESS_DRIVER_WARNINGS": "1",
    }
    if insecure:
        common_env = common_env | {JMP_GRPC_INSECURE: "1"}
    if passphrase:
        common_env = common_env | {JMP_GRPC_PASSPHRASE: passphrase}

    if command:
        return _run_process(list(command), common_env, lease)

    init_content = _generate_shell_init(shell_name, use_profiles, j_commands)
    init_file = None
    if init_content:
        init_file = tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False)
        init_file.write(init_content)
        init_file.close()

    try:
        if shell_name.endswith("bash"):
            env = common_env | {
                "PS1": f"{ANSI_GRAY}{PROMPT_CWD} {ANSI_YELLOW}⚡{ANSI_WHITE}{context} {ANSI_YELLOW}➤{ANSI_RESET} ",
            }
            cmd = [shell]
            if init_file:
                cmd.extend(["--rcfile", init_file.name])
            elif not use_profiles:
                cmd.extend(["--norc", "--noprofile"])
            return _run_process(cmd, env, lease)

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
            init_cmd = fish_fn
            if init_file:
                init_cmd += f"; source {init_file.name}"
            return _run_process([shell, "--init-command", init_cmd], common_env, lease)

        elif shell_name == "zsh":
            env = common_env | {
                "PS1": f"%F{{8}}%1~ %F{{yellow}}⚡%F{{white}}{context} %F{{yellow}}➤%f ",
            }
            if "HISTFILE" not in env:
                env["HISTFILE"] = os.path.join(os.path.expanduser("~"), ".zsh_history")
            cmd = [shell]
            if init_file:
                cmd.extend(["--rcs", "-o", "inc_append_history", "-o", "share_history"])
                env["ZDOTDIR"] = os.path.dirname(init_file.name)
                zshrc_path = os.path.join(os.path.dirname(init_file.name), ".zshrc")
                with open(zshrc_path, "w") as f:
                    f.write(init_content)
            else:
                if not use_profiles:
                    cmd.append("--no-rcs")
                cmd.extend(["-o", "inc_append_history", "-o", "share_history"])
            return _run_process(cmd, env, lease)

        else:
            return _run_process([shell], common_env, lease)
    finally:
        if init_file:
            try:
                os.unlink(init_file.name)
            except OSError:
                pass
