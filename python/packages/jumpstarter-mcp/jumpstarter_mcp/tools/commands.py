"""MCP tools for command execution, environment export, and introspection."""

from __future__ import annotations

import logging
import shutil

import anyio
import anyio.to_thread
import click

from jumpstarter_mcp.connections import ConnectionManager

from jumpstarter.client.introspect import get_driver_methods, list_drivers, walk_click_tree
from jumpstarter.common.process import open_streaming_process

logger = logging.getLogger(__name__)

# After a timed-out command is killed, read what it already wrote for at most this
# long; a child it started may keep the pipes open indefinitely.
_DRAIN_SECONDS = 1.0


async def run_command(
    manager: ConnectionManager,
    connection_id: str,
    command: list[str],
    timeout_seconds: int = 120,
) -> dict:
    """Run a `j` subcommand against the connection.

    Executes the command as a subprocess with JUMPSTARTER_HOST set to the
    connection's socket path. Captures stdout, stderr, and exit code.
    """
    conn = manager.get_connection(connection_id)
    j_path = shutil.which("j")
    if j_path is None:
        return {"error": "j CLI binary not found in PATH"}

    env = {
        "JUMPSTARTER_HOST": conn.socket_path,
        "JMP_DRIVERS_ALLOW": "UNSAFE" if conn.unsafe else ",".join(conn.allow),
        "_JMP_SUPPRESS_DRIVER_WARNINGS": "1",
    }

    import os

    full_env = {**os.environ, **env}

    logger.info("Running command: j %s (timeout=%ds)", " ".join(command), timeout_seconds)
    output = {"stdout": bytearray(), "stderr": bytearray()}
    try:
        async with open_streaming_process([j_path, *command], env=full_env, stop_grace=0) as proc:
            with anyio.move_on_after(timeout_seconds) as timeout:
                async for chunk in proc:
                    output[chunk.source] += chunk.data
                exit_code = await proc.wait()
            if timeout.cancelled_caught:
                exit_code = await proc.stop()
                with anyio.move_on_after(_DRAIN_SECONDS):
                    async for chunk in proc:
                        output[chunk.source] += chunk.data
    except Exception:
        logger.exception("Command failed unexpectedly: j %s", " ".join(command))
        raise

    result = {
        "exit_code": exit_code,
        "stdout": output["stdout"].decode(errors="replace"),
        "stderr": output["stderr"].decode(errors="replace"),
        "command": [j_path, *command],
    }
    if timeout.cancelled_caught:
        logger.warning("Command timed out after %ds: j %s", timeout_seconds, " ".join(command))
        result |= {"timed_out": True, "timeout_seconds": timeout_seconds}
    else:
        logger.info("Command finished: j %s -> exit_code=%s", " ".join(command), exit_code)
    return result


async def get_env(
    manager: ConnectionManager,
    connection_id: str,
) -> dict:
    """Return environment variables and paths for direct shell/Python interaction."""
    return manager.get_env(connection_id)


async def explore(
    manager: ConnectionManager,
    connection_id: str,
    command_path: list[str] | None = None,
) -> dict:
    """Walk the Click command tree for a connected device.

    If command_path is provided, drills into that subtree.
    """
    conn = manager.get_connection(connection_id)
    client = conn.client

    if not hasattr(client, "cli"):
        return {"error": "Client does not have a CLI interface"}

    cli_cmd = await anyio.to_thread.run_sync(client.cli)  # ty: ignore[invalid-argument-type]

    if command_path:
        for name in command_path:
            if isinstance(cli_cmd, click.Group) and name in cli_cmd.commands:
                cli_cmd = cli_cmd.commands[name]
            else:
                return {"error": f"Command '{name}' not found at this level"}

    return walk_click_tree(cli_cmd)


async def drivers(
    manager: ConnectionManager,
    connection_id: str,
) -> list[dict]:
    """List all drivers in the client tree."""
    conn = manager.get_connection(connection_id)
    # Off the loop thread, as explore() does. These walk a sync client facade
    # over a blocking portal; they read attributes statically today and so do
    # not dispatch, but a driver property that ever did would deadlock the loop.
    return await anyio.to_thread.run_sync(list_drivers, conn.client)


async def driver_methods(
    manager: ConnectionManager,
    connection_id: str,
    driver_path: list[str],
) -> dict:
    """Inspect methods on a specific driver."""
    conn = manager.get_connection(connection_id)
    return await anyio.to_thread.run_sync(get_driver_methods, conn.client, driver_path)


async def list_events(
    manager: ConnectionManager,
    max_count: int = 50,
) -> str:
    """Drain and return buffered lease and connection events as JSON."""
    import json

    events = manager.drain_events(max_count)
    return json.dumps(events, indent=2)
