"""Jumpstarter MCP server exposing hardware management tools over stdio."""

from __future__ import annotations

import json
import logging

from anyio import ClosedResourceError
from mcp.server.fastmcp import FastMCP

from jumpstarter_mcp.connections import ConnectionManager
from jumpstarter_mcp.tools import commands as cmd_tools
from jumpstarter_mcp.tools import connections as conn_tools
from jumpstarter_mcp.tools import leases as lease_tools

from jumpstarter.config.client import ClientConfigV1Alpha1

logger = logging.getLogger(__name__)

SERVER_INSTRUCTIONS = """\
Jumpstarter provides remote access to physical hardware devices through a
controller that manages leases and exporters.

Typical workflow:
1. jmp_list_leases to see existing leases, or jmp_create_lease to get a new one
2. jmp_connect with the lease ID to establish a persistent connection
3. jmp_explore to discover what CLI commands are available for this device
4. jmp_run to execute commands (power control, SSH, serial, storage, etc.)
5. jmp_disconnect and jmp_delete_lease when done

Each device type exposes different commands. Always explore before assuming
what's available. Common patterns:
- Power: ["power", "on"], ["power", "off"], ["power", "cycle"]
- SSH: ["ssh", "--", "your", "command", "here"]
- Storage: ["storage", "flash", "/path/to/image"]
- Serial: ["serial", "pipe"] (streaming -- use a short timeout_seconds, e.g. 10-15)

jmp_run has a timeout_seconds parameter (default 120). For streaming/blocking
commands like "serial pipe", set a short timeout_seconds so the command is
killed after capturing available output rather than hanging.

Connections are persistent -- create once, run many commands against it.

For deeper inspection:
- jmp_drivers shows the Python driver object tree (class names, descriptions, methods)
- jmp_driver_methods drills into a specific driver to show method signatures,
  docstrings, parameters, and ready-to-use call examples

For advanced/independent usage, call jmp_get_env to get the raw environment
variables and Python/j paths. This lets you:
- Run j commands directly in the shell (faster for batch operations)
- Write and execute Python scripts using the jumpstarter API
- The Python path returned points to a venv with all jumpstarter packages
  installed, so `import jumpstarter` just works
- Use jmp_driver_methods to get exact method signatures for Python code

IMPORTANT -- Python code examples:
When generating Python examples for the user, ALWAYS use the env() helper
from jumpstarter.utils.env. This assumes the script runs under a jumpstarter
shell where JUMPSTARTER_HOST is already set (via jmp_get_env or `j shell`).

NEVER use ClientConfigV1Alpha1, lease(), or connect() in examples -- those
are for standalone automation, not interactive use.

Canonical pattern:

    from jumpstarter.utils.env import env

    with env() as client:
        client.power.on()
        result = client.ssh.run(opts, ["uname", "-a"])
        print(result.stdout)
        client.power.off()

The env() context manager reads JUMPSTARTER_HOST from the environment and
returns the same client object with access to all drivers (power, ssh,
serial, storage, tcp, etc.). Use jmp_driver_methods to discover exact
method signatures for code examples.
"""


def _load_config() -> ClientConfigV1Alpha1:
    """Load client config using the same resolution as jmp CLI."""
    from pydantic import ValidationError

    from jumpstarter.config.user import UserConfigV1Alpha1

    config = None
    try:
        config = ClientConfigV1Alpha1()
    except ValidationError:
        pass

    if config is None:
        config = UserConfigV1Alpha1.load_or_create().config.current_client

    if config is None:
        raise RuntimeError(
            "No jumpstarter client config found. "
            "Run 'jmp config set-client <name>' or set JUMPSTARTER_* environment variables."
        )

    return config


def _register_lease_tools(mcp: FastMCP) -> None:
    """Register lease and exporter management tools."""

    @mcp.tool()
    async def jmp_list_exporters(
        selector: str | None = None,
        include_leases: bool = True,
        include_online: bool = True,
    ) -> str:
        """List exporters registered on the Jumpstarter controller.

        Shows available hardware devices with their labels, online status,
        and current lease information.

        Args:
            selector: Optional label selector to filter exporters (e.g. "target=qemu")
            include_leases: Include current lease info for each exporter (default: True)
            include_online: Include online/offline status (default: True)
        """
        config = _load_config()
        result = await lease_tools.list_exporters(
            config,
            selector=selector,
            include_leases=include_leases,
            include_online=include_online,
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def jmp_list_leases(
        selector: str | None = None,
    ) -> str:
        """List active leases from the Jumpstarter controller.

        Args:
            selector: Optional label selector to filter leases
        """
        config = _load_config()
        result = await lease_tools.list_leases(config, selector=selector)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def jmp_create_lease(
        duration_seconds: int = 1800,
        selector: str | None = None,
        exporter_name: str | None = None,
    ) -> str:
        """Create a new lease for a hardware device.

        Args:
            duration_seconds: Lease duration in seconds (default: 1800 = 30 minutes)
            selector: Label selector to match exporters (e.g. "board=qemu")
            exporter_name: Specific exporter name to lease
        """
        if not selector and not exporter_name:
            return json.dumps({"error": "One of selector or exporter_name is required"})
        config = _load_config()
        result = await lease_tools.create_lease(
            config, duration_seconds=duration_seconds, selector=selector, exporter_name=exporter_name
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def jmp_delete_lease(lease_id: str) -> str:
        """Delete/release a lease.

        Args:
            lease_id: Name of the lease to delete
        """
        config = _load_config()
        result = await lease_tools.delete_lease(config, lease_id=lease_id)
        return json.dumps(result, indent=2)


def _capture_session_for_notifications(mcp: FastMCP, manager: ConnectionManager) -> None:
    """Try to capture the MCP session so background tasks can send log notifications."""
    if manager._log_callback is not None:
        return
    try:
        ctx = mcp.get_context()
        if ctx.request_context:
            session = ctx.request_context.session

            async def _log(level: str, message: str) -> None:
                try:
                    await session.send_log_message(level=level, data=message, logger="jumpstarter")
                except Exception:
                    pass

            manager.set_log_callback(_log)
    except (LookupError, AttributeError):
        pass


def _register_connection_tools(mcp: FastMCP, manager: ConnectionManager) -> None:
    """Register connection management tools."""

    @mcp.tool()
    async def jmp_connect(
        lease_id: str | None = None,
        selector: str | None = None,
        exporter_name: str | None = None,
        duration_seconds: int = 1800,
    ) -> str:
        """Connect to a hardware device, establishing a persistent background connection.

        Creates or acquires a lease, starts a background Unix socket, and returns
        a connection ID plus a summary of available CLI commands and drivers.

        Args:
            lease_id: Existing lease name to connect to
            selector: Label selector to create a new lease (e.g. "board=qemu")
            exporter_name: Specific exporter name to create a new lease
            duration_seconds: Lease duration in seconds (default: 1800 = 30 minutes)
        """
        _capture_session_for_notifications(mcp, manager)
        if not lease_id and not selector and not exporter_name:
            return json.dumps({"error": "One of lease_id, selector, or exporter_name is required"})
        config = _load_config()
        result = await conn_tools.connect(
            config,
            manager,
            lease_id=lease_id,
            selector=selector,
            exporter_name=exporter_name,
            duration_seconds=duration_seconds,
        )
        return json.dumps(result, indent=2, default=str)

    @mcp.tool()
    async def jmp_disconnect(connection_id: str) -> str:
        """Disconnect from a device and tear down the background connection.

        Args:
            connection_id: ID of the connection to disconnect
        """
        result = await conn_tools.disconnect(manager, connection_id)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def jmp_list_connections() -> str:
        """List all active persistent connections."""
        result = conn_tools.list_connections(manager)
        return json.dumps(result, indent=2)


def _register_command_tools(mcp: FastMCP, manager: ConnectionManager) -> None:
    """Register command execution and environment tools."""

    @mcp.tool()
    async def jmp_run(
        connection_id: str,
        command: list[str],
        timeout_seconds: int = 120,
    ) -> str:
        """Run a CLI command on a connected device.

        Executes a `j` subcommand against the connection. Captures stdout, stderr,
        and exit code. The process is killed when timeout_seconds is reached.

        For streaming commands like "serial pipe" that never exit on their own,
        use a short timeout_seconds (e.g. 10-15) to capture available output.

        Args:
            connection_id: ID of the active connection
            command: Command parts as a list (e.g. ["power", "on"] or ["ssh", "--", "uname", "-a"])
            timeout_seconds: Maximum execution time in seconds (default: 120).
                Use a short value for streaming commands like "serial pipe".
        """
        result = await cmd_tools.run_command(manager, connection_id, command, timeout_seconds)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def jmp_get_env(connection_id: str) -> str:
        """Get environment variables, paths, and code examples for this connection.

        Returns:
        - env vars and shell_example for running j CLI against the current MCP connection
        - standalone_python_example showing how to write a script with its own lease
        - Python/venv paths for direct execution

        Args:
            connection_id: ID of the active connection
        """
        result = await cmd_tools.get_env(manager, connection_id)
        return json.dumps(result, indent=2)


def _register_discovery_tools(mcp: FastMCP, manager: ConnectionManager) -> None:
    """Register discovery and introspection tools."""

    @mcp.tool()
    async def jmp_explore(
        connection_id: str,
        command_path: list[str] | None = None,
    ) -> str:
        """Explore available CLI commands for a connected device.

        Walks the Click command tree to show command names, help text, parameters,
        and nested subcommands. If command_path is provided, drills into that subtree.

        Args:
            connection_id: ID of the active connection
            command_path: Optional path to drill into (e.g. ["storage"] to see storage subcommands)
        """
        result = await cmd_tools.explore(manager, connection_id, command_path)
        return json.dumps(result, indent=2, default=str)

    @mcp.tool()
    async def jmp_drivers(connection_id: str) -> str:
        """List all driver objects in the connected device's driver tree.

        Returns a flat list with driver path, Python class, description, and method names.

        Args:
            connection_id: ID of the active connection
        """
        result = await cmd_tools.drivers(manager, connection_id)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def jmp_driver_methods(
        connection_id: str,
        driver_path: list[str],
    ) -> str:
        """Inspect methods on a specific driver client.

        Returns detailed method information including signatures, docstrings,
        parameters with types/defaults, and ready-to-use call examples.

        Args:
            connection_id: ID of the active connection
            driver_path: Path to the driver in the children tree (e.g. ["power"] or ["storage"])
        """
        result = await cmd_tools.driver_methods(manager, connection_id, driver_path)
        return json.dumps(result, indent=2)


def create_server() -> tuple[FastMCP, ConnectionManager]:
    """Create the MCP server and register all tools."""
    mcp = FastMCP(
        "jumpstarter",
        instructions=SERVER_INSTRUCTIONS,
    )
    manager = ConnectionManager()

    _register_lease_tools(mcp)
    _register_connection_tools(mcp, manager)
    _register_command_tools(mcp, manager)
    _register_discovery_tools(mcp, manager)

    return mcp, manager


def _setup_logging() -> None:
    """Configure logging with both stderr and file output.

    Adds a file handler to the root logger regardless of whether
    other handlers (e.g. from the mcp library) are already present.
    """
    from pathlib import Path

    log_dir = Path.home() / ".jumpstarter" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "mcp-server.log"

    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    file_handler = logging.FileHandler(log_file, mode="a")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(fmt))

    root = logging.getLogger()
    root.addHandler(file_handler)

    logging.getLogger("jumpstarter_mcp").setLevel(logging.DEBUG)
    logging.getLogger("jumpstarter").setLevel(logging.DEBUG)
    logging.getLogger("mcp").setLevel(logging.WARNING)

    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            handler.setLevel(logging.WARNING)


def _is_closed_resource_error(exc_group: BaseExceptionGroup) -> bool:
    """Check if an ExceptionGroup contains only ClosedResourceError."""
    from anyio import ClosedResourceError

    for exc in exc_group.exceptions:
        if isinstance(exc, BaseExceptionGroup):
            if not _is_closed_resource_error(exc):
                return False
        elif not isinstance(exc, ClosedResourceError):
            return False
    return True


async def run_server():
    """Run the MCP server with stdio transport."""
    _setup_logging()
    logger.info("Jumpstarter MCP server starting")
    mcp, manager = create_server()
    try:
        async with manager.running():
            await mcp.run_stdio_async()
    except BaseException as exc:
        if isinstance(exc, ClosedResourceError):
            logger.info("MCP client disconnected (stdio closed)")
        elif isinstance(exc, BaseExceptionGroup) and _is_closed_resource_error(exc):
            logger.info("MCP client disconnected (stdio closed)")
        else:
            logger.exception("MCP server crashed")
            raise
    finally:
        logger.info("Jumpstarter MCP server shutting down")
