import os
import re
import shlex
import shutil
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


_SAFE_COMMAND_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_j_commands(j_commands: list[str] | None) -> list[str] | None:
    """Filter j_commands to only include safe alphanumeric names."""
    if j_commands is None:
        return None
    return [cmd for cmd in j_commands if _SAFE_COMMAND_NAME.match(cmd)]


def _resolve_cli_paths() -> tuple[str, str, str]:
    """Resolve absolute paths for jmp, jmp-admin, and j CLI tools."""
    jmp = shutil.which("jmp") or "jmp"
    jmp_admin = shutil.which("jmp-admin") or "jmp-admin"
    j = shutil.which("j") or "j"
    return jmp, jmp_admin, j


def _generate_shell_init(shell_name: str, use_profiles: bool, j_commands: list[str] | None = None) -> str:
    """Generate shell-specific init script content for completion and profile sourcing."""
    j_commands = _validate_j_commands(j_commands)
    jmp, jmp_admin, j = _resolve_cli_paths()
    if shell_name.endswith("bash"):
        lines = []
        if use_profiles:
            lines.append('[ -f ~/.bashrc ] && source ~/.bashrc')
        lines.append(f'eval "$({jmp} completion bash 2>/dev/null)"')
        lines.append(f'eval "$({jmp_admin} completion bash 2>/dev/null)"')
        if j_commands:
            cmds = " ".join(j_commands)
            completion_fn = (
                f'_j_completion() {{ [[ ${{COMP_CWORD}} -eq 1 ]]'
                f' && COMPREPLY=($(compgen -W "{cmds}" -- "${{COMP_WORDS[COMP_CWORD]}}")); }}'
            )
            lines.append(completion_fn)
            lines.append("complete -o default -F _j_completion j")
        else:
            lines.append(f'eval "$({j} completion bash 2>/dev/null)"')
        return "\n".join(lines) + "\n"

    elif shell_name.endswith("zsh"):
        lines = []
        if use_profiles:
            lines.append('[ -f ~/.zshrc ] && source ~/.zshrc')
        lines.append("autoload -Uz compinit && compinit")
        lines.append(f'eval "$({jmp} completion zsh 2>/dev/null)"')
        lines.append(f'eval "$({jmp_admin} completion zsh 2>/dev/null)"')
        if j_commands:
            cmds = " ".join(j_commands)
            lines.append(f"compdef '_arguments \"1:subcommand:({cmds})\"' j")
        else:
            lines.append(f'eval "$({j} completion zsh 2>/dev/null)"')
        return "\n".join(lines) + "\n"

    elif shell_name.endswith("fish"):
        lines = []
        lines.append(f"{jmp} completion fish 2>/dev/null | source")
        lines.append(f"{jmp_admin} completion fish 2>/dev/null | source")
        if j_commands:
            for cmd in j_commands:
                lines.append(f"complete -c j -f -n '__fish_use_subcommand' -a '{cmd}'")
        else:
            lines.append(f"{j} completion fish 2>/dev/null | source")
        return "\n".join(lines) + "\n"

    return ""


def _launch_bash(shell, init_content, use_profiles, common_env, context, lease):
    """Launch a bash shell with completion init and custom prompt."""
    env = common_env | {
        "_JMP_SHELL_CONTEXT": context,
        "PS1": f"{ANSI_GRAY}{PROMPT_CWD} {ANSI_YELLOW}⚡{ANSI_WHITE}{context} {ANSI_YELLOW}➤{ANSI_RESET} ",
    }
    cmd = [shell]
    if not init_content:
        if not use_profiles:
            cmd.extend(["--norc", "--noprofile"])
        return _run_process(cmd, env, lease)

    init_content += (
        f'PS1="{ANSI_GRAY}{PROMPT_CWD} {ANSI_YELLOW}⚡{ANSI_WHITE}'
        '$_JMP_SHELL_CONTEXT'
        f' {ANSI_YELLOW}➤{ANSI_RESET} "\n'
    )
    init_file = tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False)
    try:
        init_file.write(init_content)
        init_file.close()
        cmd.extend(["--rcfile", init_file.name])
        return _run_process(cmd, env, lease)
    finally:
        try:
            os.unlink(init_file.name)
        except OSError:
            pass


def _launch_fish(shell, init_content, common_env, context, lease):
    """Launch a fish shell with completion init and custom prompt."""
    fish_env = common_env | {"_JMP_SHELL_CONTEXT": context}
    fish_fn = (
        "function fish_prompt; "
        "set_color grey; "
        'printf "%s" (basename $PWD); '
        "set_color yellow; "
        'printf "⚡"; '
        "set_color white; "
        'printf "%s" "$_JMP_SHELL_CONTEXT"; '
        "set_color yellow; "
        'printf "➤ "; '
        "set_color normal; "
        "end"
    )
    init_cmd = fish_fn
    if not init_content:
        return _run_process([shell, "--init-command", init_cmd], fish_env, lease)

    init_file = tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False)
    try:
        init_file.write(init_content)
        init_file.close()
        fish_env["_JMP_SHELL_INIT"] = init_file.name
        init_cmd += '; source "$_JMP_SHELL_INIT"'
        return _run_process([shell, "--init-command", init_cmd], fish_env, lease)
    finally:
        try:
            os.unlink(init_file.name)
        except OSError:
            pass


def _launch_zsh(shell, init_content, common_env, context, lease, use_profiles):
    """Launch a zsh shell with completion init, custom prompt, and ZDOTDIR management."""
    env = common_env | {
        "_JMP_SHELL_CONTEXT": context,
        "PS1": f"%F{{8}}%1~ %F{{yellow}}⚡%F{{white}}{context} %F{{yellow}}➤%f ",
    }
    if "HISTFILE" not in env:
        env["HISTFILE"] = os.path.join(os.path.expanduser("~"), ".zsh_history")
    cmd = [shell]
    tmpdir = None
    if init_content:
        init_content += (
            'PROMPT="%F{8}%1~ %F{yellow}⚡%F{white}'
            '${_JMP_SHELL_CONTEXT} %F{yellow}➤%f "\n'
        )
        tmpdir = tempfile.mkdtemp()
        original_zdotdir = env.get("ZDOTDIR", os.path.expanduser("~"))
        original_zshenv = os.path.join(original_zdotdir, ".zshenv")
        zshenv_path = os.path.join(tmpdir, ".zshenv")
        with open(zshenv_path, "w") as f:
            f.write(f"[ -f {shlex.quote(original_zshenv)} ] && source {shlex.quote(original_zshenv)}\n")
        zshrc_path = os.path.join(tmpdir, ".zshrc")
        with open(zshrc_path, "w") as f:
            f.write(f"ZDOTDIR={shlex.quote(original_zdotdir)}\n")
            f.write(init_content)
        cmd.extend(["--rcs", "-o", "inc_append_history", "-o", "share_history"])
        env["ZDOTDIR"] = tmpdir
    else:
        if not use_profiles:
            cmd.append("--no-rcs")
        cmd.extend(["-o", "inc_append_history", "-o", "share_history"])
    try:
        return _run_process(cmd, env, lease)
    finally:
        if tmpdir:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)


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
    j_commands: list[str] | None = None,
) -> int:
    """Launch an interactive shell with Jumpstarter environment and completions."""
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

    if shell_name.endswith("zsh"):
        return _launch_zsh(shell, init_content, common_env, context, lease, use_profiles)

    if shell_name.endswith("bash"):
        return _launch_bash(shell, init_content, use_profiles, common_env, context, lease)

    if shell_name.endswith("fish"):
        return _launch_fish(shell, init_content, common_env, context, lease)

    return _run_process([shell], common_env, lease)
