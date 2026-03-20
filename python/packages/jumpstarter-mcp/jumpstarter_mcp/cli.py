"""Click CLI commands for `jmp mcp`."""

from __future__ import annotations

import anyio
import click


@click.group("mcp")
def mcp():
    """MCP server for AI agent interaction with Jumpstarter hardware."""


@mcp.command("serve")
def serve():
    """Start the MCP server with stdio transport.

    This is meant to be invoked by an MCP-compatible host (e.g. Cursor)
    as a subprocess. All communication happens over stdin/stdout.
    """
    from jumpstarter_mcp.server import run_server

    anyio.run(run_server)
