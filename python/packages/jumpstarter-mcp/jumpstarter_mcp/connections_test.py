"""Tests for ConnectionManager's isolation between concurrent connections."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import anyio
import pytest

from jumpstarter_mcp.connections import ConnectionManager

# Sockets whose fake client should raise on teardown, simulating a transport
# error (a closed gRPC channel, a vanished unix socket, ...) surfacing after
# the connection already reported itself as ready.
_RAISE_ON_TEARDOWN: set[str] = set()


@dataclass
class FakeLease:
    name: str
    exporter_name: str
    allow: list[str] = field(default_factory=list)
    unsafe: bool = True
    lease_transferred: bool = False
    lease_ended: bool = False
    lease_ending_callback: object = None

    @asynccontextmanager
    async def serve_unix_async(self):
        yield f"/tmp/{self.name}.sock"

    @asynccontextmanager
    async def monitor_async(self):
        yield


@dataclass
class FakeConfig:
    lease: FakeLease

    @asynccontextmanager
    async def lease_async(self, selector=None, exporter_name=None, lease_name=None, duration=None, portal=None):
        yield self.lease


@asynccontextmanager
async def _fake_client_from_path(path, portal, stack, allow, unsafe):
    yield object()
    if path in _RAISE_ON_TEARDOWN:
        raise RuntimeError(f"simulated transport teardown error on {path}")


@pytest.mark.asyncio
async def test_connection_teardown_failure_does_not_kill_other_connections(monkeypatch):
    """One connection's post-startup failure must not tear down its siblings.

    ConnectionManager.running() owns a single anyio task group shared by every
    connect() call, so an unhandled exception in one connection's background
    task cancels the whole group unless that connection is isolated from it.
    """
    monkeypatch.setattr("jumpstarter_mcp.connections.client_from_path", _fake_client_from_path)
    _RAISE_ON_TEARDOWN.clear()

    manager = ConnectionManager()
    async with manager.running():
        config_a = FakeConfig(FakeLease("lease-a", "exporter-a"))
        config_b = FakeConfig(FakeLease("lease-b", "exporter-b"))
        conn_a = await manager.connect(config_a, lease_name="lease-a")  # ty: ignore[invalid-argument-type]
        conn_b = await manager.connect(config_b, lease_name="lease-b")  # ty: ignore[invalid-argument-type]

        _RAISE_ON_TEARDOWN.add(conn_a.socket_path)
        await manager.disconnect(conn_a.id)

        # Give connection A's background task room to run its teardown (where
        # the fake client raises) and, on unfixed code, for that exception to
        # cancel the rest of the shared task group.
        for _ in range(50):
            await anyio.sleep(0.01)

        assert conn_b.id in manager.connections, "connection B was cancelled by connection A's unrelated failure"
