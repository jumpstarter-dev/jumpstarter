"""MCP tools for connection management."""

from __future__ import annotations

import contextlib
import logging
from datetime import timedelta

from jumpstarter_mcp import catalogue
from jumpstarter_mcp.connections import ConnectionManager

from jumpstarter.config.client import ClientConfigV1Alpha1

logger = logging.getLogger(__name__)


async def connect(
    config: ClientConfigV1Alpha1,
    manager: ConnectionManager,
    lease_id: str | None = None,
    selector: str | None = None,
    exporter_name: str | None = None,
    duration_seconds: int = 1800,
) -> dict:
    """Connect to a device, returning connection info and a CLI tree summary."""
    try:
        conn = await manager.connect(
            config=config,
            lease_name=lease_id,
            selector=selector,
            exporter_name=exporter_name,
            duration=timedelta(seconds=duration_seconds),
        )
    except ConnectionError as exc:
        logger.warning("Connection failed: %s", exc)
        return {"error": str(exc)}
    except Exception as exc:
        logger.exception("Unexpected connection failure")
        return {"error": f"Failed to connect: {exc}"}

    # What the device can do, shortly: its drivers and the names of their tools. Their arguments are asked for
    # when they are needed (jmp_tools), and the whole command tree only when someone wants it (jmp_explore): the
    # answer to a connection is read by an agent every time, whatever it goes on to use.
    tools: dict[str, list[str]] | None = None
    with contextlib.suppress(Exception):
        from anyio import to_thread

        tools = await to_thread.run_sync(_tool_names, conn.client)

    return {
        "connection_id": conn.id,
        "lease_name": conn.lease_name,
        "exporter_name": conn.exporter_name,
        "socket_path": conn.socket_path,
        "tools": tools,
        "next": (
            "jmp_tools(connection_id, driver) gives a driver's tools with their arguments; call them with jmp_read "
            "(tools that only read) or jmp_call. Pass this connection_id with every call."
        ),
    }


def _tool_names(client) -> dict[str, list[str]]:
    """The tools of each driver of a lease, by the driver's path (``""`` is the device itself)."""
    names: dict[str, list[str]] = {}
    for path, node in catalogue.walk(client):
        offered = [command.tool for command in catalogue.driver_commands(path, node)]
        if offered:
            names[".".join(path)] = offered
    return names


async def disconnect(
    manager: ConnectionManager,
    connection_id: str,
) -> dict:
    """Disconnect from a device."""
    await manager.disconnect(connection_id)
    return {"connection_id": connection_id, "status": "disconnected"}


def list_connections(manager: ConnectionManager) -> list[dict]:
    """List active connections."""
    return manager.list_connections()
