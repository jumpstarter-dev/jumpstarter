import logging
import ntpath
import os
import shutil
import signal
import sys
from base64 import b64encode
from contextlib import ExitStack, asynccontextmanager, contextmanager, suppress
from datetime import timedelta
from functools import partial
from subprocess import Popen
from typing import TYPE_CHECKING

from anyio.from_thread import BlockingPortal, start_blocking_portal

from jumpstarter.client import client_from_path
from jumpstarter.common.display import display_options
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

logger = logging.getLogger(__name__)


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
    with (
        start_blocking_portal() as portal,
        ExitStack() as stack,
        portal.wrap_async_context_manager(serve_async(root_device, portal, stack)) as client,
    ):
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

# Context and display preferences arrive as environment data. Keeping them out
# of the script prevents exporter names from becoming PowerShell expressions.
# EncodedCommand accepts UTF-16LE on both PowerShell 7 and Windows PowerShell 5.
_POWERSHELL_PROMPT = """
function global:prompt {
    $directory = Split-Path -Leaf $PWD.Path
    if (-not $directory) { $directory = $PWD.Path }
    if ($env:_JMP_PROMPT_NO_COLOR -eq '1') {
        return ('{0} {1} {2} {3} ' -f $directory, $env:_JMP_PROMPT_BOLT,
            $env:_JMP_PROMPT_CONTEXT, $env:_JMP_PROMPT_ARROW)
    }
    Write-Host -NoNewline ($directory + ' ') -ForegroundColor DarkGray
    Write-Host -NoNewline $env:_JMP_PROMPT_BOLT -ForegroundColor Yellow
    Write-Host -NoNewline ($env:_JMP_PROMPT_CONTEXT + ' ') -ForegroundColor White
    Write-Host -NoNewline $env:_JMP_PROMPT_ARROW -ForegroundColor Yellow
    return ' '
}
"""


def lease_ending_handler(process: Popen, lease, remaining_time, tree=None) -> None:
    """Lease ending handler to terminate a process when lease ends.

    Args:
        process: The process to terminate
        lease: The lease instance
        remaining_time: Time remaining until lease expiration
        tree: On Windows, the Job Object holding the process and its descendants
    """

    if remaining_time <= timedelta(0):
        try:
            if tree is not None:
                # Like SIGHUP reaching a POSIX shell's jobs, stop the whole tree.
                tree.close()
            elif sys.platform == "win32":
                process.terminate()
            else:
                process.send_signal(signal.SIGHUP)
        except (ProcessLookupError, OSError):
            pass  # Process already terminated


def _contain_process_tree(process: Popen):
    """On Windows, hold a new child and the processes it starts in a Job Object.

    The child is assigned right after creation, before a shell or command has
    initialized far enough to start processes of its own. Returns None where a
    Job Object is unavailable; the child then runs uncontained.
    """
    if sys.platform != "win32":
        return None
    from jumpstarter_core.process import ChildProcessTree

    try:
        return ChildProcessTree(int(process._handle))
    except OSError as exc:
        logger.debug("Cannot contain process %d in a job object: %s", process.pid, exc)
        return None


@contextmanager
def _foreground_child_owns_ctrl_c():
    """Leave console Ctrl+C to the foreground child while waiting for it on Windows.

    Windows delivers Ctrl+C to every process attached to the console, so the
    key that interrupts a command in the child shell would otherwise also cancel
    the jmp session. POSIX shells take over the terminal's foreground process
    group instead, so jmp does not receive that SIGINT there. The child already
    exists, so it does not inherit this setting.
    """
    if sys.platform != "win32":
        yield
        return
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ignoring = bool(kernel32.SetConsoleCtrlHandler(None, True))
    try:
        yield
    finally:
        if ignoring:
            kernel32.SetConsoleCtrlHandler(None, False)


def _run_process(
    cmd: list[str],
    env: dict[str, str],
    lease=None,
) -> int:
    """Helper to run a process with an option to set a lease ending callback."""
    try:
        process = Popen(cmd, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, env=env)
    except FileNotFoundError:
        print(f"Error: command not found: {cmd[0]}", file=sys.stderr)
        return 127
    except PermissionError:
        print(f"Error: permission denied: {cmd[0]}", file=sys.stderr)
        return 126
    except OSError as exc:
        print(f"Error: cannot execute {cmd[0]}: {exc}", file=sys.stderr)
        return 126
    tree = _contain_process_tree(process)
    if lease is not None:
        lease.lease_ending_callback = partial(lease_ending_handler, process, tree=tree)
    try:
        with _foreground_child_owns_ctrl_c():
            returncode = process.wait()
    finally:
        if tree is not None:
            # As with a POSIX shell's jobs, background processes outlive a normal exit.
            with suppress(OSError):
                tree.release()
    if returncode < 0:
        # wait() reports signal deaths as -N; report them as a shell does. Log
        # the signal too: 137 alone cannot be told from a command exiting 137.
        signum = -returncode
        returncode = 128 + signum
        logger.debug("command %s killed by signal %d, reporting %d", cmd[0], signum, returncode)
    else:
        logger.debug("command %s exited with %d", cmd[0], returncode)
    return returncode


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
        JUMPSTARTER_HOST: os.fspath(host),
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


def _bash_ps1(context: str, bolt: str, arrow: str, no_color: bool) -> str:
    """Build the bash ``PS1`` for the jmp shell prompt."""
    if no_color:
        return f"{PROMPT_CWD} {bolt} {context} {arrow} "
    return (
        f"{ANSI_GRAY}{PROMPT_CWD} "
        f"{ANSI_YELLOW}{bolt}"
        f"{ANSI_WHITE}{context} "
        f"{ANSI_YELLOW}{arrow}"
        f"{ANSI_RESET} "
    )


def _fish_prompt_fn(context: str, bolt: str, arrow: str, no_color: bool) -> str:
    """Build the fish ``fish_prompt`` function for the jmp shell prompt."""
    if no_color:
        return (
            "function fish_prompt; "
            'printf "%s " (basename $PWD); '
            f'printf "{bolt}"; '
            f'printf "{context}"; '
            f'printf "{arrow} "; '
            "end"
        )
    return (
        "function fish_prompt; "
        "set_color grey; "
        'printf "%s " (basename $PWD); '
        "set_color yellow; "
        f'printf "{bolt}"; '
        "set_color white; "
        f'printf "{context}"; '
        "set_color yellow; "
        f'printf "{arrow} "; '
        "set_color normal; "
        "end"
    )


def _zsh_ps1(context: str, bolt: str, arrow: str, no_color: bool) -> str:
    """Build the zsh ``PS1`` for the jmp shell prompt."""
    if no_color:
        return f"%1~ {bolt} {context} {arrow} "
    return f"%F{{8}}%1~ %F{{yellow}}{bolt}%F{{white}}{context} %F{{yellow}}{arrow}%f "


def _default_shell() -> str:
    if sys.platform == "win32":
        return shutil.which("pwsh") or shutil.which("powershell") or os.environ.get("COMSPEC", "cmd.exe")
    return "bash"


def _shell_name(shell: str) -> str:
    if sys.platform == "win32":
        return ntpath.basename(shell).lower().removesuffix(".exe")
    return os.path.basename(shell)


def _windows_shell_command(shell: str, use_profiles: bool, context: str) -> tuple[list[str], dict[str, str]] | None:
    if sys.platform != "win32":
        return None
    match _shell_name(shell):
        case "pwsh" | "powershell":
            opts = display_options()
            env = {
                "_JMP_PROMPT_CONTEXT": context,
                "_JMP_PROMPT_BOLT": "^" if opts.no_icons else "⚡",
                "_JMP_PROMPT_ARROW": ">" if opts.no_icons else "➤",
                "_JMP_PROMPT_NO_COLOR": "1" if opts.no_color else "0",
            }
            command = [shell, "-NoLogo"] + ([] if use_profiles else ["-NoProfile"])
            encoded_prompt = b64encode(_POWERSHELL_PROMPT.encode("utf-16-le")).decode("ascii")
            command.extend(["-NoExit", "-EncodedCommand", encoded_prompt])
            return command, env
        case "cmd":
            return [shell] + ([] if use_profiles else ["/D"]), {}
        case _:
            return None


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
    motd: str | None = None,
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
        motd: Optional message of the day printed before interactive shells

    Returns:
        The exit code of the shell or command process
    """

    common_env = _build_common_env(
        host, allow, unsafe, lease=lease, insecure=insecure, passphrase=passphrase
    )

    if command:
        return _run_process(list(command), common_env, lease)

    # Only interactive sessions need a shell; resolving one searches PATH on Windows.
    shell = os.environ.get("SHELL") or _default_shell()
    shell_name = _shell_name(shell)

    if motd:
        print(motd, flush=True)

    windows_shell = _windows_shell_command(shell, use_profiles, context)
    if windows_shell is not None:
        cmd, prompt_env = windows_shell
        return _run_process(cmd, common_env | prompt_env, lease)

    opts = display_options()
    bolt = "^" if opts.no_icons else "⚡"
    arrow = ">" if opts.no_icons else "➤"

    if shell_name.endswith("bash"):
        env = common_env | {"PS1": _bash_ps1(context, bolt, arrow, opts.no_color)}
        cmd = [shell]
        if not use_profiles:
            cmd.extend(["--norc", "--noprofile"])
        return _run_process(cmd, env, lease)

    elif shell_name == "fish":
        cmd = [shell, "--init-command", _fish_prompt_fn(context, bolt, arrow, opts.no_color)]
        return _run_process(cmd, common_env, lease)

    elif shell_name == "zsh":
        env = common_env | {"PS1": _zsh_ps1(context, bolt, arrow, opts.no_color)}
        if "HISTFILE" not in env:
            env["HISTFILE"] = os.path.join(os.path.expanduser("~"), ".zsh_history")

        cmd = [shell]
        if not use_profiles:
            cmd.append("--no-rcs")
        cmd.extend(["-o", "inc_append_history", "-o", "share_history"])
        return _run_process(cmd, env, lease)

    else:
        return _run_process([shell], common_env, lease)
