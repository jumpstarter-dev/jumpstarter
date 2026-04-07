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
from jumpstarter.config.env import (
    JMP_DRIVERS_ALLOW,
    JMP_EXPORTER,
    JMP_EXPORTER_LABELS,
    JMP_GRPC_INSECURE,
    JMP_GRPC_PASSPHRASE,
    JMP_LEASE,
    JUMPSTARTER_HOST,
)
from jumpstarter.exporter import Session
from jumpstarter.utils.env import ExporterMetadata, env, env_with_metadata

if TYPE_CHECKING:
    from jumpstarter.driver import Driver

__all__ = ["ExporterMetadata", "env", "env_with_metadata"]


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


def _lease_env_vars(lease) -> dict[str, str]:
    """Extract environment variables from a lease object."""
    env_vars: dict[str, str] = {}
    env_vars[JMP_EXPORTER] = lease.exporter_name
    if lease.name:
        env_vars[JMP_LEASE] = lease.name
    if lease.exporter_labels:
        env_vars[JMP_EXPORTER_LABELS] = ",".join(
            f"{k}={v}" for k, v in sorted(lease.exporter_labels.items())
        )
    return env_vars


def _build_common_env(
    host: str,
    allow: list[str],
    unsafe: bool,
    *,
    lease=None,
    insecure: bool = False,
    passphrase: str | None = None,
) -> dict[str, str]:
    """Build the base environment dict for shell/command processes."""
    env = os.environ | {
        JUMPSTARTER_HOST: host,
        JMP_DRIVERS_ALLOW: "UNSAFE" if unsafe else ",".join(allow),
        "_JMP_SUPPRESS_DRIVER_WARNINGS": "1",  # Already warned during client initialization
    }
    if insecure:
        env = env | {JMP_GRPC_INSECURE: "1"}
    if passphrase:
        env = env | {JMP_GRPC_PASSPHRASE: passphrase}
    if lease is not None:
        env.update(_lease_env_vars(lease))
    return env


def _completion_init_lines(shell_name: str, completion_commands: list[tuple[str, str]] | None) -> str:
    if not completion_commands:
        return ""

    lines = []
    for prog_name, complete_var in completion_commands:
        if shell_name == "fish":
            lines.append(f"{complete_var}=fish_source {prog_name} | source")
        else:
            source_type = "zsh_source" if shell_name == "zsh" else "bash_source"
            lines.append(f'eval "$({complete_var}={source_type} {prog_name})"')
    return "\n".join(lines)


def _launch_bash(shell, context, env, init_script, use_profiles, lease):
    env = env | {
        "PS1": f"{ANSI_GRAY}{PROMPT_CWD} {ANSI_YELLOW}⚡{ANSI_WHITE}{context} {ANSI_YELLOW}➤{ANSI_RESET} ",
    }
    cmd = [shell, "--noprofile"]
    if init_script:
        init_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", prefix="jmp-init-", delete=False
        )
        init_file.write(init_script + "\n")
        init_file.close()
        cmd.extend(["--rcfile", init_file.name])
    elif not use_profiles:
        cmd.append("--norc")
    try:
        return _run_process(cmd, env, lease)
    finally:
        if init_script:
            os.unlink(init_file.name)


def _launch_fish(shell, context, env, init_script, lease):
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
    if init_script:
        fish_fn = init_script + "; " + fish_fn
    cmd = [shell, "--init-command", fish_fn]
    return _run_process(cmd, env, lease)


def _launch_zsh(shell, context, env, init_script, use_profiles, lease):
    env = env | {
        "PS1": f"%F{{8}}%1~ %F{{yellow}}⚡%F{{white}}{context} %F{{yellow}}➤%f ",
    }
    if "HISTFILE" not in env:
        env["HISTFILE"] = os.path.join(os.path.expanduser("~"), ".zsh_history")

    cmd = [shell]
    zdotdir = None
    if init_script:
        zdotdir = tempfile.mkdtemp(prefix="jmp-zsh-")
        with open(os.path.join(zdotdir, ".zshrc"), "w") as f:
            f.write(init_script + "\n")
        env["ZDOTDIR"] = zdotdir
    elif not use_profiles:
        cmd.append("--no-rcs")
    cmd.extend(["-o", "inc_append_history", "-o", "share_history"])
    try:
        return _run_process(cmd, env, lease)
    finally:
        if zdotdir:
            import shutil

            shutil.rmtree(zdotdir, ignore_errors=True)


def launch_shell(
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
    completion_commands: list[tuple[str, str]] | None = None,
) -> int:
    """Launch a shell with a custom prompt indicating the exporter type.

    Args:
        host: The jumpstarter host path
        context: The context of the shell (e.g. "local" or exporter name)
        allow: List of allowed drivers
        unsafe: Whether to allow drivers outside of the allow list
        use_profiles: Whether to load shell profile files
        command: Optional command to run instead of launching an interactive shell
        lease: Optional Lease object to set up lease ending callback
        completion_commands: List of (prog_name, complete_var) tuples for shell completion setup

    Returns:
        The exit code of the shell or command process
    """

    shell = os.environ.get("SHELL", "bash")
    shell_name = os.path.basename(shell)

    common_env = _build_common_env(
        host, allow, unsafe, lease=lease, insecure=insecure, passphrase=passphrase
    )

    if command:
        return _run_process(list(command), common_env, lease)

    init_script = _completion_init_lines(shell_name, completion_commands)

    if shell_name.endswith("bash"):
        return _launch_bash(shell, context, common_env, init_script, use_profiles, lease)
    elif shell_name == "fish":
        return _launch_fish(shell, context, common_env, init_script, lease)
    elif shell_name == "zsh":
        return _launch_zsh(shell, context, common_env, init_script, use_profiles, lease)
    else:
        return _run_process([shell], common_env, lease)
