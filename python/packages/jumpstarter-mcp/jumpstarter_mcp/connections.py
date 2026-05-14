from __future__ import annotations

import logging
import shutil
import sys
import sysconfig
import uuid
from collections.abc import Awaitable, Callable
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

import anyio
import anyio.abc
import anyio.from_thread
from anyio.from_thread import BlockingPortal

from jumpstarter.client.client import client_from_path
from jumpstarter.config.client import ClientConfigV1Alpha1

logger = logging.getLogger(__name__)


@dataclass
class Connection:
    id: str
    lease_name: str
    exporter_name: str
    socket_path: str
    allow: list[str]
    unsafe: bool
    created_at: datetime
    client: object  # DriverClient

    @property
    def uptime_seconds(self) -> float:
        return (datetime.now() - self.created_at).total_seconds()


def _unwrap_exception(exc: BaseException) -> BaseException:
    """Unwrap single-exception ExceptionGroups to find the root cause."""
    while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
        exc = exc.exceptions[0]
    return exc


def _check_lease_error(lease) -> None:
    """Raise a descriptive ConnectionError if the lease was transferred or expired."""
    if lease is None:
        return
    if lease.lease_transferred:
        raise ConnectionError(
            f"Lease {lease.name} has been transferred to another client. "
            "The session is no longer valid."
        ) from None
    if lease.lease_ended:
        raise ConnectionError(f"Lease {lease.name} has expired.") from None


class ConnectionManager:
    """Manages persistent background connections to exporters via unix sockets."""

    def __init__(self):
        self._connections: dict[str, Connection] = {}
        self._task_group: anyio.abc.TaskGroup | None = None
        self._portals: dict[str, BlockingPortal] = {}
        self._stacks: dict[str, ExitStack] = {}
        self._cleanup_events: dict[str, anyio.Event] = {}
        self._log_callback: Callable[[str, str], Awaitable[None]] | None = None

    def set_log_callback(self, callback: Callable[[str, str], Awaitable[None]]) -> None:
        """Set an async callback for sending MCP log notifications.

        Signature: async def callback(level: str, message: str) -> None
        """
        self._log_callback = callback

    async def _send_log(self, level: str, message: str) -> None:
        """Send a log notification via MCP if a callback is configured."""
        if self._log_callback is not None:
            try:
                await self._log_callback(level, message)
            except Exception:
                logger.debug("Failed to send MCP log notification: %s", message)

    @property
    def connections(self) -> dict[str, Connection]:
        return self._connections

    async def _forward_lease_notifications(
        self,
        notify_recv: anyio.abc.ObjectReceiveStream,
        connection_id: str,
        event: anyio.Event,
    ) -> None:
        """Forward lease ending notifications from the sync callback to MCP."""
        async for name, exporter, remaining in notify_recv:
            if remaining <= timedelta(0):
                await self._send_log(
                    "error",
                    f"Lease {name} for {exporter} has expired. "
                    f"Connection {connection_id} is no longer valid.",
                )
                event.set()
            else:
                mins = max(1, int(remaining.total_seconds() // 60))
                await self._send_log(
                    "warning",
                    f"Lease {name} for {exporter} will expire in ~{mins} minute(s).",
                )

    async def _watch_lease_transfer(
        self,
        lease,
        conn: Connection,
        connection_id: str,
        event: anyio.Event,
    ) -> None:
        """Poll for lease transfer and notify via MCP when detected."""
        while not event.is_set():
            if lease.lease_transferred:
                await self._send_log(
                    "error",
                    f"Lease {lease.name} for {conn.exporter_name} "
                    f"has been transferred to another client. "
                    f"Connection {connection_id} is no longer valid.",
                )
                event.set()
                return
            await anyio.sleep(5)

    async def connect(
        self,
        config: ClientConfigV1Alpha1,
        lease_name: str | None = None,
        selector: str | None = None,
        exporter_name: str | None = None,
        duration: timedelta = timedelta(minutes=30),
    ) -> Connection:
        """Create a new persistent connection to an exporter.

        Acquires a lease, starts serve_unix_async in the background,
        creates a client, and stores everything for later use.
        """
        connection_id = str(uuid.uuid4())[:8]
        logger.info(
            "Connecting %s (lease=%s, selector=%s, exporter=%s)",
            connection_id, lease_name, selector, exporter_name,
        )
        event = anyio.Event()

        async def _run_connection(task_status=anyio.TASK_STATUS_IGNORED):
            lease_ref = None
            try:
                async with anyio.from_thread.BlockingPortal() as portal:
                    self._portals[connection_id] = portal
                    async with config.lease_async(
                        selector=selector,
                        exporter_name=exporter_name,
                        lease_name=lease_name,
                        duration=duration,
                        portal=portal,
                    ) as lease:
                        lease_ref = lease
                        conn = await self._setup_connection(
                            config, lease, portal, connection_id, event, task_status,
                        )
                        logger.info("Connection %s tearing down (%s)", connection_id, conn.exporter_name)
            except BaseException:
                _check_lease_error(lease_ref)
                raise
            finally:
                self._connections.pop(connection_id, None)
                self._portals.pop(connection_id, None)
                self._stacks.pop(connection_id, None)
                self._cleanup_events.pop(connection_id, None)

        self._cleanup_events[connection_id] = event

        if self._task_group is None:
            raise RuntimeError("ConnectionManager not started - use 'async with manager.running()'")

        try:
            conn = await self._task_group.start(_run_connection)
        except BaseException as exc:
            self._cleanup_events.pop(connection_id, None)
            unwrapped = _unwrap_exception(exc)
            if isinstance(unwrapped, ConnectionError):
                raise unwrapped from None
            raise ConnectionError(f"Failed to connect: {unwrapped}") from unwrapped
        return conn

    async def _setup_connection(
        self,
        config: ClientConfigV1Alpha1,
        lease,
        portal: BlockingPortal,
        connection_id: str,
        event: anyio.Event,
        task_status,
    ) -> Connection:
        """Wire up the Unix socket, client, and notification watchers for a lease."""
        notify_send, notify_recv = anyio.create_memory_object_stream[tuple[str, str, timedelta]](16)  # ty: ignore[call-non-callable]

        def _on_lease_ending(lease_obj, remaining):
            try:
                notify_send.send_nowait((
                    lease_obj.name,
                    getattr(lease_obj, "exporter_name", "unknown"),
                    remaining,
                ))
            except (anyio.WouldBlock, anyio.ClosedResourceError):
                pass

        lease.lease_ending_callback = _on_lease_ending

        async with lease.serve_unix_async() as path:
            async with lease.monitor_async():
                with ExitStack() as stack:
                    self._stacks[connection_id] = stack
                    async with client_from_path(
                        path, portal, stack,
                        allow=lease.allow, unsafe=lease.unsafe,
                    ) as client:
                        conn = Connection(
                            id=connection_id,
                            lease_name=lease.name,
                            exporter_name=lease.exporter_name,
                            socket_path=str(path),
                            allow=lease.allow,
                            unsafe=lease.unsafe,
                            created_at=datetime.now(),
                            client=client,
                        )
                        self._connections[connection_id] = conn
                        logger.info(
                            "Connected %s to exporter %s (socket=%s)",
                            connection_id, lease.exporter_name, path,
                        )

                        async with anyio.create_task_group() as notify_tg:
                            notify_tg.start_soon(
                                self._forward_lease_notifications, notify_recv, connection_id, event,
                            )
                            notify_tg.start_soon(
                                self._watch_lease_transfer, lease, conn, connection_id, event,
                            )
                            task_status.started(conn)
                            await event.wait()
                            notify_tg.cancel_scope.cancel()

                        await notify_send.aclose()
        return conn

    async def disconnect(self, connection_id: str) -> None:
        """Tear down a connection and clean up resources."""
        if connection_id not in self._connections:
            raise KeyError(f"No connection with id {connection_id}")
        logger.info("Disconnecting %s", connection_id)

        event = self._cleanup_events.pop(connection_id, None)
        if event:
            event.set()

        self._connections.pop(connection_id, None)
        self._portals.pop(connection_id, None)
        self._stacks.pop(connection_id, None)

    def list_connections(self) -> list[dict]:
        """Return a structured list of active connections."""
        return [
            {
                "connection_id": conn.id,
                "lease_name": conn.lease_name,
                "exporter_name": conn.exporter_name,
                "socket_path": conn.socket_path,
                "uptime_seconds": conn.uptime_seconds,
                "created_at": conn.created_at.isoformat(),
            }
            for conn in self._connections.values()
        ]

    def get_connection(self, connection_id: str) -> Connection:
        """Get a connection by ID."""
        if connection_id not in self._connections:
            raise KeyError(f"No connection with id {connection_id}")
        return self._connections[connection_id]

    def get_env(self, connection_id: str) -> dict:
        """Return environment info for direct shell/Python interaction."""
        conn = self.get_connection(connection_id)
        python_path = sys.executable
        j_path = shutil.which("j")
        venv_path = sys.prefix
        site_pkgs = sysconfig.get_paths()["purelib"]

        env_vars = {
            "JUMPSTARTER_HOST": conn.socket_path,
            "JMP_DRIVERS_ALLOW": "UNSAFE" if conn.unsafe else ",".join(conn.allow),
            "_JMP_SUPPRESS_DRIVER_WARNINGS": "1",
        }

        python_example = (
            'from jumpstarter.utils.env import env\n'
            '\n'
            'with env() as client:\n'
            '    client.power.on()\n'
            '    client.power.off()\n'
        )

        return {
            "connection_id": conn.id,
            "lease_name": conn.lease_name,
            "exporter_name": conn.exporter_name,
            "env": env_vars,
            "python_path": python_path,
            "j_path": j_path,
            "venv_path": venv_path,
            "site_packages": site_pkgs,
            "shell_example": f"JUMPSTARTER_HOST={conn.socket_path} {j_path} power on",
            "python_example": python_example,
            "note": (
                "Run shell commands or Python scripts with the env vars set "
                "(e.g. via jmp_run or the shell). The env() helper reads "
                "JUMPSTARTER_HOST from the environment automatically."
            ),
        }

    @asynccontextmanager
    async def running(self):
        """Context manager that keeps the connection manager's task group alive."""
        async with anyio.create_task_group() as tg:
            self._task_group = tg
            try:
                yield self
            finally:
                for event in self._cleanup_events.values():
                    event.set()
                self._cleanup_events.clear()
                self._connections.clear()
                self._portals.clear()
                self._stacks.clear()
                self._task_group = None
