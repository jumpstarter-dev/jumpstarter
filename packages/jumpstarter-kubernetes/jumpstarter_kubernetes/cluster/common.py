"""Common utilities and types for cluster operations."""

import asyncio
import os
from typing import Literal, Optional

from ..exceptions import ClusterTypeValidationError

ClusterType = Literal["kind"] | Literal["minikube"]


def validate_cluster_type(
    kind: Optional[str], minikube: Optional[str]
) -> Optional[ClusterType]:
    """Validate cluster type selection - returns None if neither is specified."""
    if kind and minikube:
        raise ClusterTypeValidationError('You can only select one local cluster type "kind" or "minikube"')

    if kind is not None:
        return "kind"
    elif minikube is not None:
        return "minikube"
    else:
        return None


def get_extra_certs_path(extra_certs: Optional[str]) -> Optional[str]:
    """Get the absolute path to extra certificates file if provided.

    Expands ~ (tilde) and environment variables before resolving to absolute path.
    """
    if extra_certs is None:
        return None
    # Expand ~ and environment variables (like $HOME, $VAR) before making absolute
    expanded_path = os.path.expanduser(os.path.expandvars(extra_certs))
    return os.path.abspath(expanded_path)


def format_cluster_name(cluster_name: str) -> str:
    """Format cluster name for consistent display."""
    return cluster_name.strip()


def validate_cluster_name(cluster_name: str) -> str:
    """Validate and format cluster name."""
    if not cluster_name or not cluster_name.strip():
        from ..exceptions import ClusterNameValidationError
        raise ClusterNameValidationError(cluster_name, "Cluster name cannot be empty")
    return format_cluster_name(cluster_name)


async def run_command(cmd: list[str]) -> tuple[int, str, str]:
    """Run a command and return exit code, stdout, stderr."""
    import builtins

    # Guard against empty command list
    if not cmd:
        raise ValueError("Command list cannot be empty")

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        # Use safe decoding to avoid UnicodeDecodeError
        stdout_str = stdout.decode(errors="replace").strip()
        stderr_str = stderr.decode(errors="replace").strip()

        return process.returncode, stdout_str, stderr_str
    except builtins.FileNotFoundError as e:
        raise RuntimeError(f"Command not found: {cmd[0]}") from e
    except PermissionError as e:
        raise RuntimeError(f"Permission denied executing command: {cmd[0]} - {e}") from e
    except OSError as e:
        raise RuntimeError(f"OS error executing command '{cmd[0]}': {e}") from e


async def run_command_with_output(cmd: list[str]) -> int:
    """Run a command with real-time output streaming and return exit code."""
    import builtins

    # Guard against empty command list
    if not cmd:
        raise ValueError("Command list cannot be empty")

    try:
        process = await asyncio.create_subprocess_exec(*cmd)
        return await process.wait()
    except builtins.FileNotFoundError as e:
        raise RuntimeError(f"Command not found: {cmd[0]}") from e
    except PermissionError as e:
        raise RuntimeError(f"Permission denied executing command: {cmd[0]} - {e}") from e
    except OSError as e:
        raise RuntimeError(f"OS error executing command '{cmd[0]}': {e}") from e
