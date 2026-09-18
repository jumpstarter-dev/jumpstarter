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


@asynccontextmanager
async def _fake_client_from_path_fails_setup(path, portal, stack, allow, unsafe):
    """Never reaches the yield: simulates a failure before the connection is up."""
    raise RuntimeError("simulated setup failure before the connection ever came up")
    yield  # pragma: no cover - unreachable, keeps this an async generator


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


@pytest.mark.asyncio
async def test_lease_ended_race_during_teardown_does_not_kill_other_connections(monkeypatch):
    """A lease_ended flip that races a post-startup teardown must stay isolated.

    Lease._notify_lease_ending() can flip lease_ended to True while a
    connection is fully active. _check_lease_error() must not run ahead of
    the tracker.called check: if it does, it raises ConnectionError before
    the isolation logic ever gets a chance to run, and that ConnectionError
    escapes _run_connection exactly like an unisolated failure would,
    cancelling every sibling sharing the task group.
    """
    monkeypatch.setattr("jumpstarter_mcp.connections.client_from_path", _fake_client_from_path)
    _RAISE_ON_TEARDOWN.clear()

    manager = ConnectionManager()
    async with manager.running():
        lease_a = FakeLease("lease-a", "exporter-a")
        config_a = FakeConfig(lease_a)
        config_b = FakeConfig(FakeLease("lease-b", "exporter-b"))
        conn_a = await manager.connect(config_a, lease_name="lease-a")  # ty: ignore[invalid-argument-type]
        conn_b = await manager.connect(config_b, lease_name="lease-b")  # ty: ignore[invalid-argument-type]

        _RAISE_ON_TEARDOWN.add(conn_a.socket_path)
        # Simulate the lease-ending notification having already fired for
        # connection A, which is fully started (post-startup) at this point.
        lease_a.lease_ended = True
        await manager.disconnect(conn_a.id)

        for _ in range(50):
            await anyio.sleep(0.01)

        assert conn_b.id in manager.connections, "connection B was cancelled by connection A's lease_ended race"


@pytest.mark.asyncio
async def test_cancellation_after_startup_is_not_reported_as_a_failure(monkeypatch):
    """Cancelling the shared task group post-startup must not log a failure.

    A cancellation of the manager's own task group (e.g. server shutdown) is
    delivered to every active connection's background task as the anyio
    cancelled-exception class. Catching it with a bare `except BaseException`
    and returning instead of re-raising treats an orderly shutdown as if
    every active connection had failed, and swallows the cancellation instead
    of letting the task group unwind normally.
    """
    monkeypatch.setattr("jumpstarter_mcp.connections.client_from_path", _fake_client_from_path)
    _RAISE_ON_TEARDOWN.clear()

    sent_logs: list[tuple[str, str]] = []

    async def _capture_log(level, message):
        sent_logs.append((level, message))

    manager = ConnectionManager()
    manager.set_log_callback(_capture_log)

    async with manager.running():
        config_a = FakeConfig(FakeLease("lease-a", "exporter-a"))
        conn_a = await manager.connect(config_a, lease_name="lease-a")  # ty: ignore[invalid-argument-type]
        assert conn_a.id in manager.connections

        # Simulate the manager's own task group being cancelled from the
        # outside (e.g. server shutdown) while the connection is active.
        assert manager._task_group is not None
        manager._task_group.cancel_scope.cancel()

    assert not any("failed" in message for _level, message in sent_logs), (
        f"cancellation was caught and reported as a connection failure: {sent_logs}"
    )


@pytest.mark.asyncio
async def test_pre_startup_failure_propagates_to_connect_caller(monkeypatch):
    """A failure before task_status.started() must still reach connect().

    Guards against a future refactor of the post-startup isolation logic
    accidentally swallowing a setup-time error too.
    """
    monkeypatch.setattr("jumpstarter_mcp.connections.client_from_path", _fake_client_from_path_fails_setup)

    manager = ConnectionManager()
    async with manager.running():
        config = FakeConfig(FakeLease("lease-x", "exporter-x"))
        with pytest.raises(ConnectionError, match="simulated setup failure"):
            await manager.connect(config, lease_name="lease-x")  # ty: ignore[invalid-argument-type]
