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
    import sys

    # Redirect stdout to stderr early, before importing the server module.
    # Module-level imports in server.py (gRPC, jumpstarter, etc.) can
    # trigger logging or print output that would corrupt MCP JSON-RPC.
    # run_server() does a more thorough fd-level redirect later.
    sys.stdout = sys.stderr

    from jumpstarter_mcp.server import run_server

    anyio.run(run_server)
