"""Tests for the jumpstarter MCP server."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from subprocess import DEVNULL
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest

from jumpstarter_mcp.connections import Connection, ConnectionManager
from jumpstarter_mcp.introspect import (
    _get_public_method_names,
    get_driver_methods,
    list_drivers,
    walk_click_tree,
)
from jumpstarter_mcp.server import (
    TOKEN_REFRESH_THRESHOLD_SECONDS,
    _ensure_fresh_token,
    _setup_logging,
    create_server,
)
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

    @pytest.mark.anyio
    async def test_successful_command(self, manager_with_conn):
        from subprocess import CompletedProcess

        from jumpstarter_mcp.tools.commands import run_command

        manager, conn_id = manager_with_conn

        mock_result = CompletedProcess(
            args=["/usr/bin/j", "power", "on"],
            returncode=0,
            stdout=b"hello\n",
            stderr=b"",
        )

        with (
            patch("shutil.which", return_value="/usr/bin/j"),
            patch("jumpstarter_mcp.tools.commands.anyio.run_process", new_callable=AsyncMock, return_value=mock_result),
        ):
            result = await run_command(manager, conn_id, ["power", "on"])

        assert result["exit_code"] == 0
        assert result["stdout"] == "hello\n"
        assert "timed_out" not in result

    @pytest.mark.anyio
    async def test_timeout_discards_output(self, manager_with_conn):
        """On timeout, output is discarded and timed_out flag is set.

        anyio.run_process is cancelled on timeout, so partial stdout/stderr
        is not available. This is a design trade-off of using run_process
        (which collects output atomically) vs open_process (which would
        require manual stream management for partial capture).
        """
        from jumpstarter_mcp.tools.commands import run_command

        manager, conn_id = manager_with_conn

        async def slow_run_process(*args, **kwargs):
            import anyio

            await anyio.sleep(999)

        with (
            patch("shutil.which", return_value="/usr/bin/j"),
            patch("jumpstarter_mcp.tools.commands.anyio.run_process", side_effect=slow_run_process),
        ):
            result = await run_command(manager, conn_id, ["serial", "pipe"], timeout_seconds=1)

        assert result["timed_out"] is True
        assert result["timeout_seconds"] == 1

    @pytest.mark.anyio
    async def test_j_not_found(self, manager_with_conn):
        from jumpstarter_mcp.tools.commands import run_command

        manager, conn_id = manager_with_conn

        with patch("shutil.which", return_value=None):
            result = await run_command(manager, conn_id, ["power", "on"])

        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.anyio
    async def test_subprocess_stdin_is_devnull(self, manager_with_conn):
        """Subprocess must not inherit MCP's stdin (would consume JSON-RPC input)."""
        from subprocess import CompletedProcess

        from jumpstarter_mcp.tools.commands import run_command

        manager, conn_id = manager_with_conn

        mock_result = CompletedProcess(
            args=["/usr/bin/j", "power", "on"],
            returncode=0,
            stdout=b"ok\n",
            stderr=b"",
        )

        with (
            patch("shutil.which", return_value="/usr/bin/j"),
            patch(
                "jumpstarter_mcp.tools.commands.anyio.run_process", new_callable=AsyncMock, return_value=mock_result
            ) as mock_exec,
        ):
            await run_command(manager, conn_id, ["power", "on"])

        _, kwargs = mock_exec.call_args
        assert kwargs["stdin"] == DEVNULL


# ---------------------------------------------------------------------------
# Server creation
# ---------------------------------------------------------------------------


class TestCreateServer:
    def test_creates_server_and_manager(self):
        mcp, manager = create_server()
        assert mcp is not None
        assert isinstance(manager, ConnectionManager)


# ---------------------------------------------------------------------------
# _ensure_fresh_token
# ---------------------------------------------------------------------------


def _make_jwt_payload(exp: int | None = None, iss: str = "https://sso.example.com") -> str:
    """Build a fake JWT (header.payload.signature) with the given claims."""
    import base64
    import json as _json

    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    claims: dict = {"iss": iss}
    if exp is not None:
        claims["exp"] = exp
    payload = base64.urlsafe_b64encode(_json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}."


class TestEnsureFreshToken:
    @pytest.mark.anyio
    async def test_no_token_returns_config_unchanged(self):
        config = MagicMock()
        config.token = None
        result = await _ensure_fresh_token(config)
        assert result is config

    @pytest.mark.anyio
    async def test_valid_token_skips_refresh(self):
        future_exp = int(time.time()) + 3600
        config = MagicMock()
        config.token = _make_jwt_payload(exp=future_exp)
        config.refresh_token = "some-refresh-token"

        with patch("jumpstarter_mcp.server.ClientConfigV1Alpha1") as mock_cls:
            result = await _ensure_fresh_token(config)

        assert result is config
        mock_cls.save.assert_not_called()

    @pytest.mark.anyio
    async def test_expired_token_no_refresh_token_skips(self):
        past_exp = int(time.time()) - 60
        config = MagicMock()
        config.token = _make_jwt_payload(exp=past_exp)
        config.refresh_token = None

        result = await _ensure_fresh_token(config)
        assert result is config

    @pytest.mark.anyio
    async def test_expired_token_refreshes_successfully(self):
        past_exp = int(time.time()) - 60
        config = MagicMock()
        config.token = _make_jwt_payload(exp=past_exp)
        config.refresh_token = "old-refresh"

        new_access = "new-access-token"
        new_refresh = "new-refresh-token"
        mock_oidc = AsyncMock()
        mock_oidc.refresh_token_grant.return_value = {
            "access_token": new_access,
            "refresh_token": new_refresh,
        }

        with (
            patch("jumpstarter_mcp.server.ClientConfigV1Alpha1") as mock_cls,
            patch("jumpstarter_cli_common.oidc.Config", return_value=mock_oidc),
        ):
            result = await _ensure_fresh_token(config)

        assert result.token == new_access
        assert result.refresh_token == new_refresh
        mock_cls.save.assert_called_once_with(config)

    @pytest.mark.anyio
    async def test_expired_token_refresh_updates_only_access_when_no_new_refresh(self):
        past_exp = int(time.time()) - 60
        config = MagicMock()
        config.token = _make_jwt_payload(exp=past_exp)
        config.refresh_token = "old-refresh"

        mock_oidc = AsyncMock()
        mock_oidc.refresh_token_grant.return_value = {
            "access_token": "new-access",
        }

        with (
            patch("jumpstarter_mcp.server.ClientConfigV1Alpha1"),
            patch("jumpstarter_cli_common.oidc.Config", return_value=mock_oidc),
        ):
            result = await _ensure_fresh_token(config)

        assert result.token == "new-access"
        assert result.refresh_token == "old-refresh"

    @pytest.mark.anyio
    async def test_expired_token_refresh_failure_returns_config_unchanged(self):
        past_exp = int(time.time()) - 60
        original_token = _make_jwt_payload(exp=past_exp)
        config = MagicMock()
        config.token = original_token
        config.refresh_token = "old-refresh"

        mock_oidc = AsyncMock()
        mock_oidc.refresh_token_grant.side_effect = RuntimeError("OIDC server down")

        with (
            patch("jumpstarter_mcp.server.ClientConfigV1Alpha1") as mock_cls,
            patch("jumpstarter_cli_common.oidc.Config", return_value=mock_oidc),
        ):
            result = await _ensure_fresh_token(config)

        assert result is config
        mock_cls.save.assert_not_called()

    @pytest.mark.anyio
    async def test_near_expiry_triggers_refresh(self):
        near_exp = int(time.time()) + TOKEN_REFRESH_THRESHOLD_SECONDS - 1
        config = MagicMock()
        config.token = _make_jwt_payload(exp=near_exp)
        config.refresh_token = "refresh"

        mock_oidc = AsyncMock()
        mock_oidc.refresh_token_grant.return_value = {"access_token": "refreshed"}

        with (
            patch("jumpstarter_mcp.server.ClientConfigV1Alpha1"),
            patch("jumpstarter_cli_common.oidc.Config", return_value=mock_oidc),
        ):
            result = await _ensure_fresh_token(config)

        assert result.token == "refreshed"

    @pytest.mark.anyio
    async def test_token_without_exp_claim_skips_refresh(self):
        config = MagicMock()
        config.token = _make_jwt_payload(exp=None)
        config.refresh_token = "some-refresh"

        with patch("jumpstarter_mcp.server.ClientConfigV1Alpha1") as mock_cls:
            result = await _ensure_fresh_token(config)

        assert result is config
        mock_cls.save.assert_not_called()


# ---------------------------------------------------------------------------
# _setup_logging
# ---------------------------------------------------------------------------


class TestSetupLogging:
    def test_adds_file_handler_to_root_logger(self, tmp_path):
        root = logging.getLogger()
        handlers_before = list(root.handlers)

        try:
            with patch("pathlib.Path.home", return_value=tmp_path):
                _setup_logging()

            new_file_handlers = [
                h for h in root.handlers if isinstance(h, logging.FileHandler) and h not in handlers_before
            ]
            assert len(new_file_handlers) == 1
            assert "mcp-server.log" in new_file_handlers[0].baseFilename
        finally:
            root.handlers = handlers_before

    def test_caps_stream_handlers_to_warning(self, tmp_path):
        root = logging.getLogger()
        handlers_before = list(root.handlers)

        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.DEBUG)
        root.addHandler(stream_handler)

        try:
            with patch("pathlib.Path.home", return_value=tmp_path):
                _setup_logging()
            assert stream_handler.level == logging.WARNING
        finally:
            root.handlers = handlers_before

    def test_sets_logger_levels(self, tmp_path):
        root = logging.getLogger()
        handlers_before = list(root.handlers)

        loggers = {
            "jumpstarter_mcp": logging.getLogger("jumpstarter_mcp"),
            "jumpstarter": logging.getLogger("jumpstarter"),
            "mcp": logging.getLogger("mcp"),
        }
        saved_levels = {name: lg.level for name, lg in loggers.items()}

        try:
            with patch("pathlib.Path.home", return_value=tmp_path):
                _setup_logging()
            assert loggers["jumpstarter_mcp"].level == logging.DEBUG
            assert loggers["jumpstarter"].level == logging.DEBUG
            assert loggers["mcp"].level == logging.WARNING
        finally:
            root.handlers = handlers_before
            for name, level in saved_levels.items():
                loggers[name].setLevel(level)


# ---------------------------------------------------------------------------
# Stdout isolation (the fd-redirect pattern used by run_server)
# ---------------------------------------------------------------------------


class TestStdoutIsolation:
    def test_stray_writes_do_not_reach_saved_stdout(self):
        """After the dup/dup2 redirect, writes to sys.stdout (fd 1) must go
        to stderr, while only the saved fd still reaches the real stdout.
        This is the behavioral property that protects MCP JSON-RPC."""
        import os
        import sys

        r_out, w_out = os.pipe()
        r_err, w_err = os.pipe()

        saved_real_stdout_fd = os.dup(1)
        saved_real_stderr_fd = os.dup(2)
        saved_sys_stdout = sys.stdout
        saved_sys_stderr = sys.stderr

        try:
            os.dup2(w_out, 1)
            os.dup2(w_err, 2)
            sys.stdout = os.fdopen(1, "w", closefd=False)
            sys.stderr = os.fdopen(2, "w", closefd=False)

            # Apply the same redirect pattern as run_server():
            sys.stdout.flush()
            mcp_fd = os.dup(sys.stdout.fileno())  # save "real stdout" (pipe)
            os.dup2(sys.stderr.fileno(), sys.stdout.fileno())  # fd 1 -> stderr
            sys.stdout = sys.stderr

            # Stray write via sys.stdout -- should land in the stderr pipe
            sys.stdout.write("stray\n")
            sys.stdout.flush()

            # MCP-only write via the saved fd -- should land in the stdout pipe
            mcp_file = os.fdopen(mcp_fd, "w", closefd=True)
            mcp_file.write("mcp-json\n")
            mcp_file.flush()

            os.close(w_out)
            os.close(w_err)
            stdout_data = os.read(r_out, 4096).decode()
            stderr_data = os.read(r_err, 4096).decode()

            assert "mcp-json" in stdout_data
            assert "stray" not in stdout_data
            assert "stray" in stderr_data
        finally:
            os.dup2(saved_real_stdout_fd, 1)
            os.dup2(saved_real_stderr_fd, 2)
            os.close(saved_real_stdout_fd)
            os.close(saved_real_stderr_fd)
            os.close(r_out)
            os.close(r_err)
            sys.stdout = saved_sys_stdout
            sys.stderr = saved_sys_stderr
