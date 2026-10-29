import plistlib
import subprocess
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from anyio.from_thread import start_blocking_portal

from . import adapters
from .adapters import UsbfluxAdapter, forward_endpoint, usbflux_registered, validate_port
from .client import UsbFluxClient

TARGET = "127.0.0.1:41000"


@pytest.fixture
def client():
    with start_blocking_portal() as portal, ExitStack() as stack:
        yield UsbFluxClient(stub=MagicMock(), portal=portal, stack=stack)


@pytest.fixture
def adapter():
    with patch.object(adapters, "TcpPortforwardAdapter") as mocked:
        mocked.return_value.__enter__.return_value = ("127.0.0.1", 41000)
        mocked.return_value.__exit__.return_value = False
        yield mocked


def _completed(returncode=0, stdout="SUCCESS", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def _list_xml(*instances):
    return plistlib.dumps(
        {
            "Instances": {
                str(i): {"Host": host, "Port": port, "IsUnix": False} for i, (host, port) in enumerate(instances)
            }
        }
    )


@pytest.mark.parametrize(
    ("port", "ephemeral", "ok"), [(0, True, True), (0, False, False), (65535, False, True), (True, True, False)]
)
def test_validate_port(port, ephemeral, ok):
    if ok:
        validate_port(port, ephemeral=ephemeral)
    else:
        with pytest.raises(ValueError):
            validate_port(port, ephemeral=ephemeral)


@pytest.mark.parametrize(("local_port", "reuse"), [(0, True), (4321, False)])
def test_forward_endpoint_never_shares_a_fixed_port(client, adapter, local_port, reuse):
    with forward_endpoint(client=client, method="connect_usbmux", local_port=local_port) as target:
        assert target == TARGET
    adapter.assert_called_once_with(
        client=client, method="connect_usbmux", local_host="127.0.0.1", local_port=local_port, reuse_port=reuse
    )


def test_forward_endpoint_preserves_caller_exception_type(client, adapter):
    with pytest.raises(KeyError), forward_endpoint(client=client, method="connect_usbmux"):
        raise KeyError("caller")


def test_adapter_attaches_then_detaches(client, adapter):
    with patch.object(subprocess, "run", return_value=_completed()) as run, UsbfluxAdapter(client=client) as target:
        assert target == TARGET
        assert run.call_args.args[0] == ["usbfluxctl", "add", TARGET]
    assert run.call_args.args[0] == ["usbfluxctl", "del", TARGET]
    assert run.call_count == 2


def test_adapter_leaves_existing_registration_alone(client, adapter):
    # usbfluxctl exits 2 when the endpoint is registered already: report it, but
    # never delete a registration owned by another session.
    with (
        patch.object(subprocess, "run", return_value=_completed(2, stdout="already added")) as run,
        pytest.raises(RuntimeError, match="failed to attach .*already added"),
        UsbfluxAdapter(client=client),
    ):
        pass
    assert run.call_count == 1


def test_adapter_reports_attach_failure_and_still_cleans_up(client, adapter):
    results = [_completed(1, stdout="", stderr="no usbfluxd"), _completed(0)]
    with (
        patch.object(subprocess, "run", side_effect=results) as run,
        pytest.raises(RuntimeError, match="failed to attach .*no usbfluxd"),
        UsbfluxAdapter(client=client, usbfluxctl="/opt/bin/usbfluxctl", timeout=7),
    ):
        pass
    assert run.call_args_list[0].args[0] == ["/opt/bin/usbfluxctl", "add", TARGET]
    assert run.call_args_list[0].kwargs["timeout"] == 7
    assert run.call_args_list[1].args[0] == ["/opt/bin/usbfluxctl", "del", TARGET]


def test_adapter_missing_usbfluxctl_is_a_clear_error(client, adapter):
    with (
        patch.object(subprocess, "run", side_effect=OSError("missing")),
        pytest.raises(RuntimeError, match="Could not run usbfluxctl"),
        UsbfluxAdapter(client=client),
    ):
        pass


def test_adapter_warns_only_when_detach_fails_and_registration_remains(client, adapter):
    results = [_completed(), _completed(255, stderr="gone"), _completed(255, stdout=_list_xml(("127.0.0.1", 41000)))]
    with (
        patch.object(subprocess, "run", side_effect=results),
        patch.object(client.logger, "warning") as warning,
        UsbfluxAdapter(client=client),
    ):
        pass
    warning.assert_called_once()
    results = [_completed(), _completed(255, stderr="gone"), _completed(255, stdout=_list_xml())]
    with (
        patch.object(subprocess, "run", side_effect=results),
        patch.object(client.logger, "warning") as warning,
        UsbfluxAdapter(client=client),
    ):
        pass
    warning.assert_not_called()


@pytest.mark.parametrize(
    ("result", "registered"),
    [
        (_completed(255, stdout=_list_xml(("127.0.0.1", 41000))), True),
        (_completed(255, stdout=_list_xml(("127.0.0.1", 41001))), False),
        (_completed(255, stdout=b"not a plist"), True),
        (OSError("usbfluxctl missing"), True),
    ],
)
def test_usbflux_registered(result, registered):
    with patch.object(subprocess, "run", side_effect=[result]):
        assert usbflux_registered("usbfluxctl", TARGET) is registered
