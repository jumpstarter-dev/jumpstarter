import os
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anyio.from_thread import start_blocking_portal
from click.testing import CliRunner

from . import adapters
from . import client as client_module
from .client import UsbFluxClient

TARGET = "127.0.0.1:41000"


@pytest.fixture
def client():
    with start_blocking_portal() as portal, ExitStack() as stack:
        device = UsbFluxClient(stub=MagicMock(), portal=portal, stack=stack)
        info = {"present": True, "udid": "leased-device", "protocols": ["usbmux"]}
        with patch.object(device, "call", MagicMock(return_value=info)):
            yield device


@pytest.fixture
def adapter():
    with patch.object(adapters, "TcpPortforwardAdapter") as mocked:
        mocked.return_value.__enter__.return_value = ("127.0.0.1", 41000)
        mocked.return_value.__exit__.return_value = False
        yield mocked


def test_info_restores_integer_device_id(client):
    client.call.return_value = {"present": True, "udid": "leased-device", "device_id": 6.0}
    assert client.info()["device_id"] == 6
    assert type(client.info()["device_id"]) is int
    client.call.return_value = {"present": False, "udid": "leased-device", "device_id": None}
    assert client.info()["device_id"] is None


def test_serve_binds_loopback_for_connect_usbmux(client, adapter):
    with client.serve() as target:
        assert target == TARGET
    adapter.assert_called_once_with(
        client=client, method="connect_usbmux", local_host="127.0.0.1", local_port=0, reuse_port=True
    )


def test_serve_refuses_absent_device(client, adapter):
    client.call.return_value = {"present": False, "reason": "device is not present in usbmuxd"}
    with pytest.raises(RuntimeError, match="not present"), client.serve():
        pass
    adapter.assert_not_called()


def test_run_sets_usbmux_address_for_the_tool(client, adapter):
    completed = MagicMock(returncode=7)
    with patch.object(client_module.anyio, "run_process", new_callable=AsyncMock, return_value=completed) as run:
        assert client.run(["mytool", "list"]) == 7
    assert run.call_args.args[0] == ["mytool", "list"]
    assert run.call_args.kwargs["env"]["USBMUXD_SOCKET_ADDRESS"] == TARGET
    assert run.call_args.kwargs["env"]["PATH"] == os.environ["PATH"]
    with pytest.raises(ValueError):
        client.run([])


def test_cli_runs_tool_after_double_dash(client, adapter):
    completed = MagicMock(returncode=3)
    with patch.object(client_module.anyio, "run_process", new_callable=AsyncMock, return_value=completed) as run:
        result = CliRunner().invoke(client.cli(), ["--", "mytool", "--flag"])
    assert result.exit_code == 3
    assert run.call_args.args[0] == ["mytool", "--flag"]


def test_cli_rejects_subcommand_with_tool_command(client, adapter):
    result = CliRunner().invoke(client.cli(), ["info", "--", "mytool"])
    assert result.exit_code == 2
    assert "without a subcommand" in result.output
    result = CliRunner().invoke(client.cli(), ["--"])
    assert result.exit_code == 2
    assert "Provide a command" in result.output


def test_cli_info_and_serve(client, adapter):
    result = CliRunner().invoke(client.cli(), ["info"])
    assert result.exit_code == 0
    assert "udid: leased-device" in result.output
    with patch.object(client_module, "wait_for_interrupt"):
        result = CliRunner().invoke(client.cli(), ["serve", "--port", "41000"])
    assert result.exit_code == 0
    assert f"export USBMUXD_SOCKET_ADDRESS={TARGET}" in result.output
    adapter.assert_called_once_with(
        client=client, method="connect_usbmux", local_host="127.0.0.1", local_port=41000, reuse_port=False
    )


def test_cli_connect_uses_usbfluxd_adapter(client, adapter):
    with (
        patch.object(client_module, "wait_for_interrupt"),
        patch.object(adapters.subprocess, "run", return_value=MagicMock(returncode=0, stdout="", stderr="")) as run,
    ):
        result = CliRunner().invoke(client.cli(), ["connect", "--usbfluxctl", "/opt/usbfluxctl"])
    assert result.exit_code == 0, result.output
    assert f"Connected at {TARGET}" in result.output
    assert [call.args[0] for call in run.call_args_list] == [
        ["/opt/usbfluxctl", "add", TARGET],
        ["/opt/usbfluxctl", "del", TARGET],
    ]


def test_cli_connect_reports_adapter_errors(client, adapter):
    # Patch the detach warning: a log record emitted inside invoke() makes pytest's
    # live logging suspend capture, which would drop Click's error output.
    with (
        patch.object(adapters.subprocess, "run", side_effect=OSError("missing")),
        patch.object(client.logger, "warning"),
    ):
        result = CliRunner().invoke(client.cli(), ["connect"])
    assert result.exit_code == 1
    assert "Could not run usbfluxctl" in result.output
