"""Click CLI commands for `jmp mcp`."""

from __future__ import annotations

import anyio
import click


@click.group("mcp")
def mcp():
    """MCP server for AI agent interaction with Jumpstarter hardware."""


@mcp.command("serve")
@click.option(
    "--env",
    "environment",
    envvar="JMP_MCP_ENV",
    type=click.Path(),
    default=None,
    help=(
        "Run the device tools in this Python environment (a .venv, or a project directory that has one) instead "
        "of this one: it is where the drivers of your devices are installed. The server itself may then be "
        "installed once, anywhere."
    ),
)
@click.option(
    "--allow-found",
    is_flag=True,
    envvar="JMP_MCP_ALLOW_FOUND",
    help=(
        "Let the agent also use Python environments found near where the server is started (the active one, a "
        ".venv of the working directory or above). Off by default: using an environment runs a program from it, "
        "and a repository can carry a .venv of its own."
    ),
)
def serve(environment: str | None, allow_found: bool):
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

    anyio.run(run_server, environment, allow_found)


@click.group("jumpstarter-mcp", invoke_without_command=True)
@click.option("--env", "environment", envvar="JMP_MCP_ENV", type=click.Path(), default=None, help="See `serve`.")
@click.option("--allow-found", is_flag=True, envvar="JMP_MCP_ALLOW_FOUND", help="See `serve`.")
@click.pass_context
def main(ctx: click.Context, environment: str | None, allow_found: bool):
    """The Jumpstarter MCP server: `jumpstarter-mcp` starts it, as `jmp mcp serve` does.

    Install it once (`uv tool install jumpstarter-mcp`) and register `jumpstarter-mcp` in your agent's host. The
    drivers of your devices go in a Python environment of their own, per project: the server runs the device
    tools there (`--env`, or the environments file).
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(serve, environment=environment, allow_found=allow_found)


main.add_command(serve)
