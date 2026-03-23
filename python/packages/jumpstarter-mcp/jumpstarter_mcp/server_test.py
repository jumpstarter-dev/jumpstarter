"""Tests for the jumpstarter MCP server."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from unittest.mock import AsyncMock, patch

import click
import pytest

from jumpstarter_mcp.connections import Connection, ConnectionManager
from jumpstarter_mcp.introspect import (
    _get_public_method_names,
    get_driver_methods,
    list_drivers,
    walk_click_tree,
)
from jumpstarter_mcp.server import create_server
from jumpstarter_mcp.tools.leases import _lease_status

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


class FakePowerClient:
    children: dict = {}

    def on(self) -> None:
        """Power on the device."""

    def off(self) -> None:
        """Power off the device."""

    def cycle(self, wait: int = 2) -> None:
        """Power cycle the device."""

    def _private(self):
        pass

    def call(self):
        pass

    def check_exporter_status(self):
        pass


class FakeSerialClient:
    children: dict = {}

    def open(self):
        """Open serial port."""

    def pexpect(self):
        """Create pexpect adapter."""


class FakeCompositeClient:
    description = "Test composite device"

    def __init__(self):
        self.children = {
            "power": FakePowerClient(),
            "serial": FakeSerialClient(),
        }

    def __getattr__(self, name):
        try:
            return self.children[name]
        except KeyError:
            raise AttributeError(name) from None


def _make_connection(
    connection_id: str = "test-conn",
    lease_name: str = "test-lease",
    exporter_name: str = "test-exporter",
    socket_path: str = "/tmp/test.sock",
    client: object | None = None,
) -> Connection:
    return Connection(
        id=connection_id,
        lease_name=lease_name,
        exporter_name=exporter_name,
        socket_path=socket_path,
        allow=[],
        unsafe=True,
        created_at=datetime.now(),
        client=client or FakeCompositeClient(),
    )


@dataclass
class FakeCondition:
    type: str
    status: str


@dataclass
class FakeLease:
    conditions: list


# ---------------------------------------------------------------------------
# walk_click_tree
# ---------------------------------------------------------------------------


class TestWalkClickTree:
    def test_simple_command(self):
        @click.command("hello")
        @click.option("--name", help="Your name")
        def hello(name):
            """Say hello."""

        result = walk_click_tree(hello)
        assert result["name"] == "hello"
        assert result["help"] == "Say hello."
        assert len(result["params"]) == 1
        assert result["params"][0]["name"] == "name"
        assert result["params"][0]["help"] == "Your name"
        assert "subcommands" not in result

    def test_group_with_subcommands(self):
        @click.group("root")
        def root():
            """Root group."""

        @root.command("sub1")
        def sub1():
            """First sub."""

        @root.command("sub2")
        @click.option("--count", type=int, default=5)
        def sub2(count):
            """Second sub."""

        result = walk_click_tree(root)
        assert result["name"] == "root"
        assert "subcommands" in result
        assert "sub1" in result["subcommands"]
        assert "sub2" in result["subcommands"]
        assert result["subcommands"]["sub2"]["params"][0]["name"] == "count"
        assert result["subcommands"]["sub2"]["params"][0]["default"] == 5

    def test_hidden_params_excluded(self):
        @click.command("cmd")
        @click.option("--visible", help="shown")
        @click.option("--secret", hidden=True)
        def cmd(visible, secret):
            pass

        result = walk_click_tree(cmd)
        names = [p["name"] for p in result["params"]]
        assert "visible" in names
        assert "secret" not in names


# ---------------------------------------------------------------------------
# Introspection: _get_public_method_names / list_drivers / get_driver_methods
# ---------------------------------------------------------------------------


class TestGetPublicMethodNames:
    def test_filters_private_and_base_methods(self):
        names = _get_public_method_names(FakePowerClient())
        assert "on" in names
        assert "off" in names
        assert "cycle" in names
        assert "_private" not in names
        assert "call" not in names
        assert "check_exporter_status" not in names


class TestListDrivers:
    def test_flat_tree(self):
        client = FakeCompositeClient()
        result = list_drivers(client)
        paths = [d["path"] for d in result]
        assert "client" in paths
        assert "client.power" in paths
        assert "client.serial" in paths

    def test_driver_path_field(self):
        client = FakeCompositeClient()
        result = list_drivers(client)
        root = next(d for d in result if d["path"] == "client")
        assert root["driver_path"] == []
        power = next(d for d in result if d["path"] == "client.power")
        assert power["driver_path"] == ["power"]

    def test_methods_populated(self):
        client = FakeCompositeClient()
        result = list_drivers(client)
        power = next(d for d in result if d["path"] == "client.power")
        assert "on" in power["methods"]
        assert "off" in power["methods"]


class TestGetDriverMethods:
    def test_returns_method_details(self):
        client = FakeCompositeClient()
        result = get_driver_methods(client, ["power"])
        assert result["driver_path"] == ["power"]
        method_names = [m["name"] for m in result["methods"]]
        assert "on" in method_names
        assert "off" in method_names
        assert "cycle" in method_names

    def test_call_example_uses_dot_notation(self):
        client = FakeCompositeClient()
        result = get_driver_methods(client, ["power"])
        cycle = next(m for m in result["methods"] if m["name"] == "cycle")
        assert "client.power.cycle(" in cycle["call_example"]
        assert 'children["power"]' not in cycle["call_example"]

    def test_invalid_path_raises(self):
        client = FakeCompositeClient()
        with pytest.raises(KeyError, match="nonexistent"):
            get_driver_methods(client, ["nonexistent"])

    def test_docstrings_captured(self):
        client = FakeCompositeClient()
        result = get_driver_methods(client, ["power"])
        on_method = next(m for m in result["methods"] if m["name"] == "on")
        assert on_method["docstring"] == "Power on the device."

    def test_parameters_captured(self):
        client = FakeCompositeClient()
        result = get_driver_methods(client, ["power"])
        cycle = next(m for m in result["methods"] if m["name"] == "cycle")
        assert len(cycle["parameters"]) == 1
        assert cycle["parameters"][0]["name"] == "wait"
        assert cycle["parameters"][0]["default"] == "2"


# ---------------------------------------------------------------------------
# _lease_status
# ---------------------------------------------------------------------------


class TestLeaseStatus:
    def test_ready(self):
        lease = FakeLease(conditions=[FakeCondition(type="Ready", status="True")])
        assert _lease_status(lease) == "ready"

    def test_pending(self):
        lease = FakeLease(conditions=[FakeCondition(type="Pending", status="True")])
        assert _lease_status(lease) == "pending"

    def test_unsatisfiable(self):
        lease = FakeLease(conditions=[FakeCondition(type="Unsatisfiable", status="True")])
        assert _lease_status(lease) == "unsatisfiable"

    def test_unknown_when_no_conditions(self):
        lease = FakeLease(conditions=[])
        assert _lease_status(lease) == "unknown"

    def test_unknown_when_not_true(self):
        lease = FakeLease(conditions=[FakeCondition(type="Ready", status="False")])
        assert _lease_status(lease) == "unknown"


# ---------------------------------------------------------------------------
# ConnectionManager (unit, no real connections)
# ---------------------------------------------------------------------------


class TestConnectionManager:
    def test_get_connection_missing_raises(self):
        manager = ConnectionManager()
        with pytest.raises(KeyError, match="no-such-id"):
            manager.get_connection("no-such-id")

    def test_list_connections_empty(self):
        manager = ConnectionManager()
        assert manager.list_connections() == []

    def test_get_env(self):
        manager = ConnectionManager()
        conn = _make_connection()
        manager._connections[conn.id] = conn

        env = manager.get_env(conn.id)
        assert env["connection_id"] == conn.id
        assert env["lease_name"] == "test-lease"
        assert env["exporter_name"] == "test-exporter"
        assert "JUMPSTARTER_HOST" in env["env"]
        assert env["env"]["JUMPSTARTER_HOST"] == "/tmp/test.sock"
        assert "env()" in env["python_example"]
        assert "os.environ" not in env["python_example"]

    def test_list_connections_with_entry(self):
        manager = ConnectionManager()
        conn = _make_connection()
        manager._connections[conn.id] = conn

        conns = manager.list_connections()
        assert len(conns) == 1
        assert conns[0]["connection_id"] == "test-conn"
        assert conns[0]["exporter_name"] == "test-exporter"


# ---------------------------------------------------------------------------
# run_command (subprocess mocking)
# ---------------------------------------------------------------------------


class TestRunCommand:
    @pytest.fixture()
    def manager_with_conn(self):
        manager = ConnectionManager()
        conn = _make_connection()
        manager._connections[conn.id] = conn
        return manager, conn.id

    @pytest.mark.asyncio
    async def test_successful_command(self, manager_with_conn):
        from jumpstarter_mcp.tools.commands import run_command

        manager, conn_id = manager_with_conn

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"hello\n", b""))
        mock_proc.returncode = 0

        with (
            patch("shutil.which", return_value="/usr/bin/j"),
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        ):
            result = await run_command(manager, conn_id, ["power", "on"])

        assert result["exit_code"] == 0
        assert result["stdout"] == "hello\n"
        assert "timed_out" not in result

    @pytest.mark.asyncio
    async def test_timeout_captures_output(self, manager_with_conn):
        from jumpstarter_mcp.tools.commands import run_command

        manager, conn_id = manager_with_conn

        call_count = 0

        async def fake_communicate():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                await asyncio.sleep(999)
            return (b"partial", b"err")

        mock_proc = AsyncMock()
        mock_proc.communicate = fake_communicate
        mock_proc.kill = lambda: None
        mock_proc.returncode = -9

        with (
            patch("shutil.which", return_value="/usr/bin/j"),
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        ):
            result = await run_command(manager, conn_id, ["serial", "pipe"], timeout_seconds=1)

        assert result["timed_out"] is True
        assert result["timeout_seconds"] == 1
        assert result["stdout"] == "partial"

    @pytest.mark.asyncio
    async def test_j_not_found(self, manager_with_conn):
        from jumpstarter_mcp.tools.commands import run_command

        manager, conn_id = manager_with_conn

        with patch("shutil.which", return_value=None):
            result = await run_command(manager, conn_id, ["power", "on"])

        assert "error" in result
        assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# Server creation
# ---------------------------------------------------------------------------


class TestCreateServer:
    def test_creates_server_and_manager(self):
        mcp, manager = create_server()
        assert mcp is not None
        assert isinstance(manager, ConnectionManager)
