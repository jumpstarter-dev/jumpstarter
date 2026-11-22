"""MCP tools made from the drivers of a lease: what a device can do, listed and called by name.

The drivers of a leased device are known only once it is connected, and differ from one device to the next. The
catalogue (``jumpstarter_mcp.catalogue``) makes a typed tool of each of their commands, the way Robot Framework makes
keywords of them. These are the fixed tools an agent reaches them through, whatever its host does with a list of
tools that changes while it runs:

* ``jmp_tools`` lists a lease's drivers and their tools, and, for one driver, each tool's arguments;
* ``jmp_read`` calls a tool that only reads, and ``jmp_call`` any tool: two, so that a host can let the reads pass
  and ask a person before the rest.

Every call names its lease (``connection_id``): there is no "current" lease to mistake, and two leases with the same
driver have the same tool names.
"""

from __future__ import annotations

import logging
from typing import Any

import anyio.to_thread

from jumpstarter_mcp import catalogue
from jumpstarter_mcp.connections import ConnectionManager

logger = logging.getLogger(__name__)

MAX_SUMMARY = 160


def _summary(command: catalogue.Command) -> str:
    first = command.description.splitlines()[0] if command.description else ""
    return first[:MAX_SUMMARY]


def _lease(conn: Any) -> dict[str, Any]:
    """Which lease an answer is about: said in every answer, so two leases are never taken for one another."""
    return {"connection_id": conn.id, "exporter": conn.exporter_name, "lease": conn.lease_name}


def _listed(client: Any, driver: str | None) -> dict[str, Any]:
    if driver is None or driver.strip() in ("", "*"):
        drivers = []
        for path, node in catalogue.walk(client):
            offered = catalogue.driver_commands(path, node)
            if offered:
                drivers.append(
                    {
                        "driver": ".".join(path),
                        "class": catalogue.class_path(type(node)),
                        "tools": {command.tool: _summary(command) for command in offered},
                    }
                )
        return {
            "drivers": drivers,
            "next": "Call jmp_tools again with one `driver` for the arguments of its tools, then jmp_read or jmp_call.",
        }
    path = tuple(part for part in driver.replace("/", ".").split(".") if part)
    node = catalogue.resolve(client, path)
    return {
        "driver": ".".join(path),
        "class": catalogue.class_path(type(node)),
        "tools": [
            {
                "tool": command.tool,
                "description": command.description,
                "kind": command.kind,
                "read_only": command.read_only,
                "destructive": command.destructive,
                "arguments": command.schema,
                "call_with": "jmp_read" if command.read_only else "jmp_call",
            }
            for command in catalogue.driver_commands(path, node)
        ],
    }


async def list_tools(manager: ConnectionManager, connection_id: str, driver: str | None = None) -> dict:
    """The tools of a lease: its drivers with their tools, or one driver's tools with their arguments."""
    conn = manager.get_connection(connection_id)
    try:
        listed = await anyio.to_thread.run_sync(_listed, conn.client, driver)
    except catalogue.CommandError as exc:
        return {**_lease(conn), "error": str(exc)}
    return {**_lease(conn), **listed}


def _called(client: Any, tool: str, arguments: dict[str, Any] | None, reads_only: bool) -> dict[str, Any]:
    node, command = catalogue.find_tool(client, tool)
    if reads_only and not command.read_only:
        raise catalogue.CommandError(
            f"{command.tool} changes the device: call it with jmp_call (jmp_read is for tools that only read)."
        )
    return {"tool": command.tool, "driver": command.driver, "result": catalogue.invoke(node, command, arguments)}


async def call_tool(
    manager: ConnectionManager,
    connection_id: str,
    tool: str,
    arguments: dict[str, Any] | None = None,
    *,
    reads_only: bool = False,
) -> dict:
    """Calls one tool of a lease with its arguments; what the driver answered is the ``result``."""
    conn = manager.get_connection(connection_id)
    try:
        called = await anyio.to_thread.run_sync(_called, conn.client, tool, arguments, reads_only)
    except catalogue.CommandError as exc:
        return {**_lease(conn), "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - what the driver raised is the tool's answer, not the server's failure
        logger.info("%s on %s failed: %s", tool, connection_id, exc)
        return {**_lease(conn), "tool": tool, "error": f"{type(exc).__name__}: {exc}"}
    return {**_lease(conn), **called}
