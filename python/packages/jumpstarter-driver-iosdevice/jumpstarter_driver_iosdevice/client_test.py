import asyncio
import ipaddress
import json
import os
import plistlib
import socket
import ssl
import stat
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from concurrent.futures import CancelledError
from contextlib import ExitStack, asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anyio import connect_tcp
from anyio.from_thread import start_blocking_portal
from click.testing import CliRunner
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from . import client as client_module
from .client import CONNECT_TIMEOUT, DISCONNECT_TIMEOUT, IosDeviceClient, _wait_for_interrupt
from .interface import IosDeviceInterface

TARGET = "127.0.0.1:41000"


@pytest.fixture
def client():
    with start_blocking_portal() as portal, ExitStack() as stack:
        device = IosDeviceClient(stub=MagicMock(), portal=portal, stack=stack)
        info = {"present": True, "udid": "leased-device", "protocols": ["usbmux"], "forward_ports": [8100]}
        with patch.object(device, "call", MagicMock(return_value=info)):
            yield device


@pytest.fixture
def adapter():
    with patch.object(client_module, "TcpPortforwardAdapter") as mocked:
        mocked.return_value.__enter__.return_value = ("127.0.0.1", 41000)
        mocked.return_value.__exit__.return_value = False
        yield mocked


def _completed(returncode=0, stdout="SUCCESS", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


@pytest.mark.parametrize("protocol", ["usbmux", "idb"])
def test_serve_selects_native_protocol_and_binds_only_loopback(client, adapter, protocol):
    client.call.return_value["protocols"] = [protocol]
    with patch.object(subprocess, "run") as run, client.serve() as target:
        assert target == TARGET
        adapter.assert_called_once_with(
            client=client, method=f"connect_{protocol}", local_host="127.0.0.1", local_port=0
        )
    adapter.return_value.__exit__.assert_called_once()
    run.assert_not_called()


def test_serve_can_select_another_advertised_protocol(client, adapter):
    client.call.return_value["protocols"] = ["usbmux", "idb"]
    with client.serve("idb", port=1234):
        assert adapter.call_args.kwargs["method"] == "connect_idb"
        assert adapter.call_args.kwargs["local_port"] == 1234


@pytest.mark.parametrize("method", ["serve", "connect"])
def test_unsupported_protocol_refused_before_opening_listener(client, adapter, method):
    with pytest.raises(ValueError, match="supported protocols: usbmux"), getattr(client, method)("coredevice"):
        pass
    adapter.assert_not_called()


def test_absent_target_explains_failure_before_opening_listener(client, adapter):
    client.call.return_value.update(present=False, reason="Device is not connected")
    with pytest.raises(RuntimeError, match="Device is not connected"), client.serve():
        pass
    adapter.assert_not_called()


@pytest.mark.parametrize("method", ["serve", "https"])
@pytest.mark.parametrize("port", [-1, 65536, "8100", True])
def test_invalid_local_port_refused(client, adapter, method, port):
    with pytest.raises(ValueError, match="Port must"), getattr(client, method)(port=port):
        pass
    adapter.assert_not_called()


@pytest.mark.parametrize(
    ("protocol", "attach", "detach"),
    [
        ("usbmux", ["usbfluxctl", "add", TARGET], ["usbfluxctl", "del", TARGET]),
        ("idb", ["idb", "connect", "127.0.0.1", "41000"], ["idb", "disconnect", "127.0.0.1", "41000"]),
    ],
)
def test_connect_and_detach_use_upstream_command_syntax(client, adapter, protocol, attach, detach):
    client.call.return_value["protocols"] = [protocol]
    with patch.object(subprocess, "run", return_value=_completed()) as run:
        with client.connect() as target:
            assert target == TARGET
            assert [call.args[0] for call in run.call_args_list] == [attach]
        assert [call.args[0] for call in run.call_args_list] == [attach, detach]
        assert [call.kwargs["timeout"] for call in run.call_args_list] == [CONNECT_TIMEOUT, DISCONNECT_TIMEOUT]


@pytest.mark.parametrize("protocol", ["usbmux", "idb"])
@pytest.mark.parametrize("failure", ["returncode", "timeout", "missing"])
def test_failed_attach_also_detaches(client, adapter, protocol, failure):
    client.call.return_value["protocols"] = [protocol]
    first_result = {
        "returncode": _completed(1, stderr="connection refused"),
        "timeout": subprocess.TimeoutExpired("attach", CONNECT_TIMEOUT),
        "missing": FileNotFoundError("tool not installed"),
    }[failure]
    with (
        patch.object(subprocess, "run", side_effect=[first_result, _completed()]) as run,
        pytest.raises(RuntimeError),
        client.connect(),
    ):
        raise AssertionError("A failed attach must not yield an endpoint")
    assert run.call_count == 2
    assert run.call_args_list[1].args[0][1] == ("del" if protocol == "usbmux" else "disconnect")
    adapter.return_value.__exit__.assert_called_once()


def test_does_not_delete_usbflux_registration_owned_by_another_session(client, adapter):
    with (
        patch.object(subprocess, "run", return_value=_completed(2, stderr="Remote is already present")) as run,
        pytest.raises(RuntimeError, match="already present"),
        client.connect(),
    ):
        pass
    run.assert_called_once()


@pytest.mark.parametrize("failure", [ValueError("body failed"), KeyboardInterrupt(), asyncio.CancelledError()])
def test_body_error_or_interrupt_detaches(client, adapter, failure):
    with (
        patch.object(subprocess, "run", return_value=_completed()) as run,
        pytest.raises(type(failure)),
        client.connect(),
    ):
        raise failure
    assert run.call_args_list[-1].args[0] == ["usbfluxctl", "del", TARGET]


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("detach", 30), OSError("gone"), _completed(1)])
def test_cleanup_failure_never_masks_session_error(client, adapter, failure):
    with (
        patch.object(subprocess, "run", side_effect=[_completed(), failure, OSError("list failed")]),
        pytest.raises(ValueError, match="session failed"),
        client.connect(),
    ):
        raise ValueError("session failed")


def _instances(*remotes):
    """``usbfluxctl list xml`` output; its list command always exits 255."""
    instances = {
        str(number): {"IsUnix": False, "Host": host, "Port": port, "Devices": []}
        for number, (host, port) in enumerate(remotes, 1)
    }
    return subprocess.CompletedProcess([], 255, stdout=plistlib.dumps({"Instances": instances}), stderr=b"")


def _detach_warnings(logger):
    return [call for call in logger.warning.call_args_list if call.args[0].startswith("Could not detach")]


@pytest.mark.parametrize(
    ("listing", "warned"),
    [
        (_instances(), False),
        (_instances(("127.0.0.1", 41001), ("127.0.0.10", 41000)), False),
        (_instances(("127.0.0.1", 41000)), True),
        (subprocess.CompletedProcess([], 255, stdout=b"", stderr=b"Failed to get list of instances."), True),
        (OSError("usbfluxctl missing"), True),
    ],
)
def test_failed_usbflux_detach_warns_only_while_still_registered(client, adapter, listing, warned):
    # Ctrl+C closes the listener first; usbfluxd then drops the remote itself.
    removed = _completed(1, stderr="Failed to remove remote instance.")
    with (
        patch.object(client, "logger") as logger,
        patch.object(subprocess, "run", side_effect=[_completed(), removed, listing]) as run,
        client.connect(),
    ):
        pass
    assert run.call_args_list[2].args[0] == ["usbfluxctl", "list", "xml"]
    assert bool(_detach_warnings(logger)) is warned


def test_failed_idb_detach_always_warns(client, adapter):
    client.call.return_value["protocols"] = ["idb"]
    detach_failed = _completed(1, stderr="not connected")
    with (
        patch.object(client, "logger") as logger,
        patch.object(subprocess, "run", side_effect=[_completed(), detach_failed]) as run,
        client.connect(),
    ):
        pass
    assert run.call_count == 2
    assert len(_detach_warnings(logger)) == 1


def test_connect_uses_custom_tool_and_timeout(client, adapter):
    with (
        patch.object(subprocess, "run", return_value=_completed()) as run,
        client.connect(usbfluxctl="/usr/local/bin/usbfluxctl", timeout=7),
    ):
        assert run.call_args.args[0][0] == "/usr/local/bin/usbfluxctl"
        assert run.call_args.kwargs["timeout"] == 7


def test_forward_uses_configured_child_and_matching_local_port(client, adapter):
    child = MagicMock()
    client.children["port_8100"] = child
    with client.forward(8100) as target:
        assert target == TARGET
        adapter.assert_called_once_with(client=child, local_host="127.0.0.1", local_port=8100)


def test_forward_can_choose_an_unused_local_port(client, adapter):
    client.children["port_8100"] = MagicMock()
    with client.forward(8100, local_port=0):
        assert adapter.call_args.kwargs["local_port"] == 0


def test_unconfigured_forward_names_configured_ports(client, adapter):
    with pytest.raises(ValueError, match="configured ports: 8100"), client.forward(9100):
        pass
    adapter.assert_not_called()


def test_info_restores_integer_ports_from_rpc_floats(client, adapter):
    # Generic RPC values carry every number as a float.
    client.call.return_value["forward_ports"] = [8100.0, 9100.0]
    info = client.info()
    assert info["forward_ports"] == [8100, 9100]
    assert all(type(port) is int for port in info["forward_ports"])
    with pytest.raises(ValueError, match=r"configured ports: 8100, 9100$"), client.forward(7000):
        pass


@pytest.mark.parametrize("protocol,variable", [("usbmux", "USBMUXD_SOCKET_ADDRESS"), ("idb", "IDB_COMPANION")])
def test_tool_environment_is_scoped_to_child_process(client, adapter, protocol, variable):
    client.call.return_value["protocols"] = [protocol]
    with (
        patch.dict(os.environ, {variable: "original", "PRESERVED": "value"}),
        patch.object(client_module.anyio, "run_process", new_callable=AsyncMock) as run,
    ):
        run.return_value = _completed(7)
        assert client.run(["my-tool", "--name", "argument with spaces"]) == 7
        assert os.environ[variable] == "original"
        assert run.call_args.args[0] == ["my-tool", "--name", "argument with spaces"]
        assert run.call_args.kwargs["env"][variable] == TARGET
        assert run.call_args.kwargs["env"]["PRESERVED"] == "value"
        assert run.call_args.kwargs["stdout"] is None
    adapter.return_value.__exit__.assert_called_once()


@pytest.mark.parametrize("failure", [FileNotFoundError("tool missing"), asyncio.CancelledError()])
def test_tool_failure_closes_listener(client, adapter, failure):
    with (
        patch.object(client_module.anyio, "run_process", new_callable=AsyncMock, side_effect=failure),
        pytest.raises((type(failure), CancelledError)),
    ):
        client.run(["missing-tool"])
    adapter.return_value.__exit__.assert_called_once()


@pytest.mark.parametrize(
    "failure", [KeyboardInterrupt(), SystemExit(), GeneratorExit(), RuntimeError("closed"), asyncio.CancelledError()]
)
def test_interrupt_unwinds_cli_wait(failure):
    _wait_for_interrupt(MagicMock(portal=MagicMock(call=MagicMock(side_effect=failure))))


def test_unexpected_wait_error_propagates():
    with pytest.raises(ValueError, match="bug"):
        _wait_for_interrupt(MagicMock(portal=MagicMock(call=MagicMock(side_effect=ValueError("bug")))))


def test_cli_info_and_surface(client):
    group = client.cli()
    assert sorted(group.commands) == ["connect", "forward", "https", "info", "serve"]
    result = CliRunner().invoke(group, ["info"])
    assert result.exit_code == 0, result.output
    assert "udid: leased-device" in result.output
    assert "protocols: ['usbmux']" in result.output


@pytest.mark.parametrize("protocol", ["usbmux", "idb"])
def test_cli_serve_prints_usable_tool_command(client, adapter, protocol):
    client.call.return_value["protocols"] = [protocol]
    with patch.object(client_module, "_wait_for_interrupt"), patch.object(subprocess, "run") as run:
        result = CliRunner().invoke(client.cli(), ["serve"])
    assert result.exit_code == 0, result.output
    expected = f"export USBMUXD_SOCKET_ADDRESS={TARGET}" if protocol == "usbmux" else "idb connect 127.0.0.1 41000"
    assert expected in result.output
    run.assert_not_called()


def test_cli_connect_disconnects_after_wait(client, adapter):
    with (
        patch.object(client_module, "_wait_for_interrupt"),
        patch.object(subprocess, "run", return_value=_completed()) as run,
    ):
        result = CliRunner().invoke(client.cli(), ["connect"])
    assert result.exit_code == 0, result.output
    assert "Disconnected" in result.output
    assert [call.args[0] for call in run.call_args_list] == [
        ["usbfluxctl", "add", TARGET],
        ["usbfluxctl", "del", TARGET],
    ]


def test_cli_invalid_protocol_prints_supported_protocols(client, adapter):
    result = CliRunner().invoke(client.cli(), ["serve", "--protocol", "coredevice"])
    assert result.exit_code == 1
    assert "supported protocols: usbmux" in result.output
    adapter.assert_not_called()


def test_cli_multiple_forwards_cleanup_if_later_port_fails(client):
    events = []

    @contextmanager
    def forward(port):
        if port == 9100:
            raise ValueError("Port 9100 is not configured")
        events.append(("open", port))
        try:
            yield f"127.0.0.1:{port}"
        finally:
            events.append(("close", port))

    client.forward = forward
    result = CliRunner().invoke(client.cli(), ["forward", "8100", "9100"])
    assert result.exit_code == 1
    assert events == [("open", 8100), ("close", 8100)]


def test_cli_command_passthrough_preserves_arguments_and_exit_code(client, adapter):
    with patch.object(client_module.anyio, "run_process", new_callable=AsyncMock, return_value=_completed(7)) as run:
        result = CliRunner().invoke(client.cli(), ["--", "info", "--help", "--", "two words"])
    assert result.exit_code == 7, result.output
    assert run.call_args.args[0] == ["info", "--help", "--", "two words"]


def test_cli_command_selects_idb_environment(client, adapter):
    client.call.return_value["protocols"] = ["usbmux", "idb"]
    with patch.object(client_module.anyio, "run_process", new_callable=AsyncMock, return_value=_completed()) as run:
        result = CliRunner().invoke(client.cli(), ["--protocol", "idb", "--", "idb", "describe"])
    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs["env"]["IDB_COMPANION"] == TARGET


def test_cli_command_requires_separator(client):
    result = CliRunner().invoke(client.cli(), ["my-tool"])
    assert result.exit_code == 2
    assert "No such command" in result.output


def test_cli_empty_command_reports_usage(client):
    result = CliRunner().invoke(client.cli(), ["--"])
    assert result.exit_code == 2
    assert "Provide a command" in result.output


def test_interface_requires_info():
    with pytest.raises(TypeError, match="abstract"):
        IosDeviceInterface()
    assert IosDeviceInterface.client() == "jumpstarter_driver_iosdevice.client.IosDeviceClient"


@pytest.mark.parametrize("method", ["serve", "connect", "forward"])
@pytest.mark.parametrize("failure", [RuntimeError("tool failed"), KeyboardInterrupt(), asyncio.CancelledError()])
def test_real_listener_preserves_caller_exception_and_closes(client, method, failure):
    client.children["port_8100"] = client
    context = client.forward(8100, local_port=0) if method == "forward" else getattr(client, method)()
    with (
        patch.object(subprocess, "run", return_value=_completed()),
        pytest.raises(type(failure)) as caught,
        context as target,
    ):
        raise failure
    assert caught.value is failure
    host, port = target.rsplit(":", 1)
    with socket.socket() as probe:
        assert probe.connect_ex((host, int(port))) != 0


@pytest.fixture(scope="module")
def https_identity():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "leased HTTPS endpoint")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return (
        certificate.public_bytes(serialization.Encoding.PEM).decode(),
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
    )


@pytest.fixture
def https_client(client, https_identity):
    client.call.return_value = {
        "ca_certificate": https_identity[0],
        "metadata": {"device": {"id": "leased-device"}, "features": ["status", {"input": True}]},
    }
    return client


def test_https_context_owns_loopback_listener_and_public_ca_only(https_client, adapter, https_identity):
    with https_client.https(port=4321) as endpoint:
        assert endpoint.url == f"https://{TARGET}"
        assert set(vars(endpoint)) == {"url", "ca_file", "metadata"}
        assert endpoint.ca_file.read_text() == https_identity[0]
        assert stat.S_IMODE(endpoint.ca_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(endpoint.ca_file.parent.stat().st_mode) == 0o700
        assert endpoint.metadata == https_client.call.return_value["metadata"]
        endpoint.metadata["features"][1]["input"] = False
        assert https_client.call.return_value["metadata"]["features"][1]["input"] is True
        https_client.call.assert_called_once_with("https_info")
        adapter.assert_called_once_with(
            client=https_client, method="connect_https", local_host="127.0.0.1", local_port=4321
        )
    assert not endpoint.ca_file.parent.exists()
    adapter.return_value.__exit__.assert_called_once()
    assert https_client.call.call_count == 1
    with pytest.raises(RuntimeError, match="context has closed"):
        endpoint.environment()


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"provider.example/options": {"formats": ["png", "custom"], "enabled": True, "ratio": 0.5, "limit": 2}},
        {"service": "wda", "capabilities": {"appium:webDriverAgentUrl": "provider-defined", "arbitrary": None}},
    ],
)
def test_https_metadata_is_provider_defined_without_vendor_defaults(https_client, adapter, metadata):
    https_client.call.return_value["metadata"] = metadata
    with https_client.https() as endpoint:
        assert endpoint.metadata == metadata
        assert json.loads(endpoint.environment({})["JUMPSTARTER_IOS_HTTPS_METADATA"]) == metadata
    assert not hasattr(IosDeviceClient, "wda")
    assert not hasattr(IosDeviceClient, "appium")


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        [],
        "value",
        {1: "value"},
        {"nested": [{1: "value"}]},
        {"nested": (1, 2)},
        {"nested": b"bytes"},
        {"nested": object()},
        {"nested": [float("nan")]},
        {"nested": float("inf")},
    ],
)
def test_invalid_https_metadata_is_rejected_before_listener(https_client, adapter, metadata):
    https_client.call.return_value["metadata"] = metadata
    with pytest.raises(ValueError, match="metadata"), https_client.https():
        pass
    adapter.assert_not_called()


def test_cyclic_https_metadata_is_rejected_before_listener(https_client, adapter):
    metadata = {}
    metadata["self"] = metadata
    https_client.call.return_value["metadata"] = metadata
    with pytest.raises(ValueError, match="cyclic"), https_client.https():
        pass
    adapter.assert_not_called()


@pytest.mark.parametrize(
    "certificate",
    [None, "not a certificate", "-----BEGIN PRIVATE KEY-----", "x" * (1024 * 1024 + 1)],
    ids=["missing", "invalid", "private", "oversized"],
)
def test_bad_or_private_https_metadata_never_opens_listener(client, adapter, certificate):
    client.call.return_value = {"ca_certificate": certificate}
    with pytest.raises(ValueError, match="public CA certificate"), client.https():
        pass
    adapter.assert_not_called()


def test_https_metadata_rejects_private_key_even_alongside_valid_certificate(client, adapter, https_identity):
    client.call.return_value = {"ca_certificate": https_identity[0] + https_identity[1].decode()}
    with pytest.raises(ValueError, match="public CA certificate"), client.https():
        pass
    adapter.assert_not_called()


def test_https_environment_preserves_existing_trust_without_mutating_environment(https_client, adapter, tmp_path):
    original_ca = tmp_path / "existing.pem"
    original_ca.write_text("# Existing configured roots\n" + https_client.call.return_value["ca_certificate"])
    original = {
        "NODE_EXTRA_CA_CERTS": str(original_ca),
        "SSL_CERT_FILE": str(original_ca),
        "REQUESTS_CA_BUNDLE": str(original_ca),
        "UNCHANGED": "value",
    }
    with https_client.https() as endpoint:
        environment = endpoint.environment(original)
        assert environment["UNCHANGED"] == "value"
        assert original["NODE_EXTRA_CA_CERTS"] == str(original_ca)
        for name in ("NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
            bundle = Path(environment[name])
            assert bundle.parent == endpoint.ca_file.parent
            assert original_ca.read_text() in bundle.read_text()
            assert endpoint.ca_file.read_text() in bundle.read_text()
            assert stat.S_IMODE(bundle.stat().st_mode) == 0o600
        assert environment["JUMPSTARTER_IOS_HTTPS_URL"] == endpoint.url
        assert json.loads(environment["JUMPSTARTER_IOS_HTTPS_METADATA"]) == endpoint.metadata
        assert environment["JUMPSTARTER_IOS_HTTPS_CA_FILE"] == str(endpoint.ca_file)
    assert not bundle.exists()
    assert original_ca.exists()


def test_https_environment_preserves_default_python_trust_and_refuses_disabled_node_tls(https_client, adapter):
    with https_client.https() as endpoint:
        env = endpoint.environment({})
        assert "SSL_CERT_FILE" not in env
        assert "REQUESTS_CA_BUNDLE" not in env
        assert env["NODE_EXTRA_CA_CERTS"] == str(endpoint.ca_file)
        assert env["JUMPSTARTER_IOS_HTTPS_URL"] == endpoint.url
        context = endpoint.ssl_context()
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname
        with pytest.raises(ValueError, match="disables HTTPS verification"):
            endpoint.environment({"NODE_TLS_REJECT_UNAUTHORIZED": "0"})


@pytest.mark.parametrize("failure", [RuntimeError("tool failed"), KeyboardInterrupt(), asyncio.CancelledError()])
def test_https_context_closes_real_listener_and_ca_on_failure(https_client, failure):
    with pytest.raises(type(failure)) as caught, https_client.https() as endpoint:
        raise failure
    assert caught.value is failure
    assert not endpoint.ca_file.parent.exists()
    host, port = endpoint.url.removeprefix("https://").split(":")
    with socket.socket() as probe:
        assert probe.connect_ex((host, int(port))) != 0


def test_https_cli_prints_json_while_public_ca_exists(https_client, adapter):
    paths = []

    def wait(_client):
        paths.extend(Path(path).parent for path in Path(tempfile.gettempdir()).glob("jumpstarter-ios-https-*/ca.pem"))

    with patch.object(client_module, "_wait_for_interrupt", side_effect=wait):
        result = CliRunner().invoke(https_client.cli(), ["https", "--port", "1234"])
    assert result.exit_code == 0, result.output
    info = json.loads(result.output)
    assert set(info) == {"url", "ca_file", "metadata"}
    assert info["metadata"] == https_client.call.return_value["metadata"]
    assert info["url"] == f"https://{TARGET}"
    assert Path(info["ca_file"]).parent in paths
    assert not Path(info["ca_file"]).exists()


def test_https_cli_command_passes_public_trust_and_preserves_exit_code(https_client, adapter):
    with patch.object(client_module.anyio, "run_process", new_callable=AsyncMock, return_value=_completed(7)) as run:
        result = CliRunner().invoke(https_client.cli(), ["https", "--", "node", "two words", "--", "arg"])
    assert result.exit_code == 7, result.output
    assert run.call_args.args[0] == ["node", "two words", "--", "arg"]
    env = run.call_args.kwargs["env"]
    assert env["JUMPSTARTER_IOS_HTTPS_URL"] == f"https://{TARGET}"
    assert not Path(env["NODE_EXTRA_CA_CERTS"]).exists()


def test_https_native_tls_crosses_opaque_adapter_and_requires_public_ca(https_client, https_identity, tmp_path):
    certificate, key = https_identity
    cert_path, key_path = tmp_path / "server.crt", tmp_path / "server.key"
    cert_path.write_text(certificate)
    key_path.write_bytes(key)

    class StatusHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"value":{"ready":true}}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), StatusHandler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_path, key_path)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    methods = []

    @asynccontextmanager
    async def stream(method):
        methods.append(method)
        async with await connect_tcp("127.0.0.1", server.server_port) as transport:
            yield transport

    https_client.stream_async = stream
    try:
        with https_client.https() as endpoint:
            with pytest.raises(urllib.error.URLError) as caught:
                urllib.request.urlopen(endpoint.url + "/status", timeout=5)
            error = caught.value
            assert isinstance(error, urllib.error.URLError)
            assert isinstance(error.reason, ssl.SSLCertVerificationError)
            with urllib.request.urlopen(
                endpoint.url + "/status", context=endpoint.ssl_context(), timeout=5
            ) as response:
                assert json.load(response) == {"value": {"ready": True}}
            address = endpoint.url.removeprefix("https://").split(":")
            with (
                socket.create_connection((address[0], int(address[1])), timeout=5) as raw,
                pytest.raises(ssl.SSLCertVerificationError),
            ):
                endpoint.ssl_context().wrap_socket(raw, server_hostname="another-device.invalid")
        assert methods == ["connect_https"] * 3
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()
