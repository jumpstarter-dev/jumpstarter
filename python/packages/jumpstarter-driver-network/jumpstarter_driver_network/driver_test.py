import os
import socket
import subprocess
import sys
from shutil import which
from unittest.mock import AsyncMock, patch

import pytest
from anyio.from_thread import start_blocking_portal
from click.testing import CliRunner

from .adapters import TcpPortforwardAdapter, UnixPortforwardAdapter
from .driver import DbusNetwork, TcpNetwork, UdpNetwork, UnixNetwork, WebsocketNetwork
from jumpstarter.common import TemporaryUnixListener
from jumpstarter.common.utils import serve


async def echo_handler(stream):
    async with stream:
        while True:
            try:
                await stream.send(await stream.receive())
            except Exception:
                pass


def test_tcp_network_portforward(tcp_echo_server):
    with serve(TcpNetwork(host=tcp_echo_server[0], port=tcp_echo_server[1])) as client:
        with TcpPortforwardAdapter(client=client) as addr:
            stream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            stream.connect(addr)
            stream.send(b"hello")
            assert stream.recv(5) == b"hello"


def test_unix_network_portforward():
    with start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(TemporaryUnixListener(echo_handler)) as inner:
            with serve(UnixNetwork(path=inner)) as client:
                with UnixPortforwardAdapter(client=client) as addr:
                    stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    stream.connect(str(addr))
                    stream.send(b"hello")
                    assert stream.recv(5) == b"hello"


def test_udp_network():
    with serve(
        UdpNetwork(
            host="127.0.0.1",
            port=8001,
        )
    ) as client:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", 8001))

            with client.stream() as stream:
                stream.send(b"hello")
                assert s.recv(5) == b"hello"


def test_unix_network():
    with start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(TemporaryUnixListener(echo_handler)) as path:
            with serve(
                UnixNetwork(
                    path=path,
                )
            ) as client:
                with client.stream() as stream:
                    stream.send(b"hello")
                    assert stream.receive() == b"hello"


@pytest.mark.skipif(which("iperf3") is None, reason="iperf3 not available")
def test_tcp_network_performance():
    with serve(
        TcpNetwork(
            host="127.0.0.1",
            port=5201,
        )
    ) as client:
        server = subprocess.Popen(
            ["iperf3", "-s"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        with TcpPortforwardAdapter(client=client) as addr:
            subprocess.run(
                [
                    "iperf3",
                    "-c",
                    addr[0],
                    "-p",
                    str(addr[1]),
                    "-t",
                    "1",
                    "--bidir",
                ],
                stdout=sys.stdout,
                stderr=sys.stderr,
            )

        server.terminate()


def test_tcp_network_direct():
    with serve(TcpNetwork(host="127.0.0.1", port=5201, enable_address=True)) as client:
        # The address method now returns the resolved IP with scheme
        addr = client.address()
        assert addr in ["tcp://127.0.0.1:5201", "tcp://localhost:5201"]


def test_udp_network_direct():
    with serve(UdpNetwork(host="127.0.0.1", port=5201, enable_address=True)) as client:
        # The address method now returns the resolved IP with scheme
        addr = client.address()
        assert addr in ["udp://127.0.0.1:5201", "udp://localhost:5201"]

def test_tcp_network_direct_disabled():
    with serve(TcpNetwork(host="127.0.0.1", port=5201, enable_address=False)) as client:
        with pytest.raises(ValueError):
            client.address()

def test_udp_network_direct_disabled():
    with serve(UdpNetwork(host="127.0.0.1", port=5201, enable_address=False)) as client:
        with pytest.raises(ValueError):
            client.address()


@pytest.mark.skipif(
    os.getenv("DBUS_SYSTEM_BUS_ADDRESS") is None and not os.path.exists("/run/dbus/system_bus_socket"),
    reason="dbus system bus not available",
)
@pytest.mark.skipif(which("busctl") is None, reason="busctl not available")
def test_dbus_network_system(monkeypatch):
    with serve(DbusNetwork(kind="system")) as client:
        assert client.kind == "system"
        oldvar = os.getenv("DBUS_SYSTEM_BUS_ADDRESS")
        with client:
            assert oldvar != os.getenv("DBUS_SYSTEM_BUS_ADDRESS")
            subprocess.run(
                ["busctl", "list", "--system", "--no-pager"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        assert oldvar == os.getenv("DBUS_SYSTEM_BUS_ADDRESS")


@pytest.mark.skipif(
    os.getenv("DBUS_SESSION_BUS_ADDRESS") is None and not os.path.exists(f"/run/user/{os.getuid()}/bus"),
    reason="dbus session bus not available",
)
@pytest.mark.skipif(which("busctl") is None, reason="busctl not available")
def test_dbus_network_session(monkeypatch):
    with serve(DbusNetwork(kind="session")) as client:
        assert client.kind == "session"
        oldvar = os.getenv("DBUS_SESSION_BUS_ADDRESS")
        with client:
            assert oldvar != os.getenv("DBUS_SESSION_BUS_ADDRESS")
            subprocess.run(
                ["busctl", "list", "--user", "--no-pager"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        assert oldvar == os.getenv("DBUS_SESSION_BUS_ADDRESS")


@pytest.mark.asyncio
async def test_websocket_network_connect():
    ws = AsyncMock()
    ws.__aenter__.return_value = ws

    with patch("websockets.connect", return_value=ws) as m:
        client =  WebsocketNetwork(url="ws://localhost/something")
        async with client.connect():
            m.assert_called_once_with("ws://localhost/something")


# IPv6 and hostname resolution tests
def test_tcp_network_ipv6_direct():
    """Test TCP network with IPv6 address."""
    with serve(TcpNetwork(host="::1", port=8080, enable_address=True)) as client:
        addr = client.address()
        assert addr == "tcp://[::1]:8080"


def test_udp_network_ipv6_direct():
    """Test UDP network with IPv6 address."""
    with serve(UdpNetwork(host="::1", port=8080, enable_address=True)) as client:
        addr = client.address()
        assert addr == "udp://[::1]:8080"


def test_tcp_network_hostname_resolution():
    """Test TCP network with hostname resolution."""
    with serve(TcpNetwork(host="localhost", port=8080, enable_address=True)) as client:
        addr = client.address()
        # Should resolve to either IPv4 or IPv6 localhost
        assert addr in ["tcp://127.0.0.1:8080", "tcp://[::1]:8080"]


def test_udp_network_hostname_resolution():
    """Test UDP network with hostname resolution."""
    with serve(UdpNetwork(host="localhost", port=8080, enable_address=True)) as client:
        addr = client.address()
        # Should resolve to either IPv4 or IPv6 localhost
        assert addr in ["udp://127.0.0.1:8080", "udp://[::1]:8080"]


def test_tcp_network_complex_ipv6():
    """Test TCP network with complex IPv6 address."""
    with serve(TcpNetwork(host="2001:db8::1", port=443, enable_address=True)) as client:
        addr = client.address()
        assert addr == "tcp://[2001:db8::1]:443"


def test_udp_network_complex_ipv6():
    """Test UDP network with complex IPv6 address."""
    with serve(UdpNetwork(host="2001:db8::1", port=443, enable_address=True)) as client:
        addr = client.address()
        assert addr == "udp://[2001:db8::1]:443"


# Test the helper functions directly
def test_format_address_function():
    """Test the _format_address helper function directly."""
    from .driver import _format_address

    # Test IPv4
    assert _format_address("127.0.0.1", 8080) == "127.0.0.1:8080"

    # Test IPv6
    assert _format_address("::1", 8080) == "[::1]:8080"
    assert _format_address("2001:db8::1", 8080) == "[2001:db8::1]:8080"

    # Test hostname (should resolve)
    localhost_result = _format_address("localhost", 8080)
    assert localhost_result in ["127.0.0.1:8080", "[::1]:8080"]


def test_is_ipv6_address_function():
    """Test the _is_ipv6_address helper function directly."""
    from .driver import _is_ipv6_address

    # Valid IPv6 addresses
    assert _is_ipv6_address("::1")
    assert _is_ipv6_address("2001:db8::1")
    assert _is_ipv6_address("::")

    # Invalid IPv6 addresses
    assert not _is_ipv6_address("127.0.0.1")
    assert not _is_ipv6_address("localhost")
    assert not _is_ipv6_address("example.com")


def test_resolve_hostname_function():
    """Test the _resolve_hostname helper function directly."""
    from .driver import _resolve_hostname

    # IP addresses should remain unchanged
    assert _resolve_hostname("127.0.0.1") == "127.0.0.1"
    assert _resolve_hostname("::1") == "::1"
    assert _resolve_hostname("2001:db8::1") == "2001:db8::1"

    # Hostnames should resolve
    localhost_result = _resolve_hostname("localhost")
    assert localhost_result in ["127.0.0.1", "::1"]


# Test client parsing functions
def test_parse_address_function():
    """Test the _parse_address helper function directly."""
    from .client import _parse_address

    # Test IPv4
    host, port = _parse_address("127.0.0.1:8080")
    assert host == "127.0.0.1"
    assert port == "8080"

    # Test IPv6
    host, port = _parse_address("[::1]:8080")
    assert host == "::1"
    assert port == "8080"

    # Test hostname
    host, port = _parse_address("localhost:8080")
    assert host == "localhost"
    assert port == "8080"

    # Test with scheme
    host, port = _parse_address("tcp://127.0.0.1:8080")
    assert host == "127.0.0.1"
    assert port == "8080"


def test_strip_scheme_function():
    """Test the _strip_scheme helper function directly."""
    from .client import _strip_scheme

    # Test TCP scheme
    assert _strip_scheme("tcp://127.0.0.1:8080") == "127.0.0.1:8080"

    # Test UDP scheme
    assert _strip_scheme("udp://[::1]:8080") == "[::1]:8080"

    # Test without scheme
    assert _strip_scheme("127.0.0.1:8080") == "127.0.0.1:8080"
    assert _strip_scheme("[::1]:8080") == "[::1]:8080"

    # Test complex IPv6
    assert _strip_scheme("tcp://[2001:db8::1]:443") == "[2001:db8::1]:443"


# CLI tests
def test_tcp_network_cli_address_command():
    """Test TCP network CLI address command."""
    with serve(TcpNetwork(host="127.0.0.1", port=8080, enable_address=True)) as client:
        cli = client.cli()
        runner = CliRunner()

        # Test full address output
        result = runner.invoke(cli, ["address"])
        assert result.exit_code == 0
        assert result.output.strip() in ["127.0.0.1:8080", "localhost:8080"]

        # Test --host option
        result = runner.invoke(cli, ["address", "--host"])
        assert result.exit_code == 0
        assert result.output.strip() in ["127.0.0.1", "localhost"]

        # Test --port option
        result = runner.invoke(cli, ["address", "--port"])
        assert result.exit_code == 0
        assert result.output.strip() == "8080"


def test_udp_network_cli_address_command():
    """Test UDP network CLI address command."""
    with serve(UdpNetwork(host="127.0.0.1", port=8080, enable_address=True)) as client:
        cli = client.cli()
        runner = CliRunner()

        # Test full address output
        result = runner.invoke(cli, ["address"])
        assert result.exit_code == 0
        assert result.output.strip() in ["127.0.0.1:8080", "localhost:8080"]

        # Test --host option
        result = runner.invoke(cli, ["address", "--host"])
        assert result.exit_code == 0
        assert result.output.strip() in ["127.0.0.1", "localhost"]

        # Test --port option
        result = runner.invoke(cli, ["address", "--port"])
        assert result.exit_code == 0
        assert result.output.strip() == "8080"


def test_tcp_network_cli_ipv6_address_command():
    """Test TCP network CLI with IPv6 address."""
    with serve(TcpNetwork(host="::1", port=8080, enable_address=True)) as client:
        cli = client.cli()
        runner = CliRunner()

        # Test full address output
        result = runner.invoke(cli, ["address"])
        assert result.exit_code == 0
        assert result.output.strip() == "[::1]:8080"

        # Test --host option
        result = runner.invoke(cli, ["address", "--host"])
        assert result.exit_code == 0
        assert result.output.strip() == "::1"

        # Test --port option
        result = runner.invoke(cli, ["address", "--port"])
        assert result.exit_code == 0
        assert result.output.strip() == "8080"


def test_udp_network_cli_ipv6_address_command():
    """Test UDP network CLI with IPv6 address."""
    with serve(UdpNetwork(host="::1", port=8080, enable_address=True)) as client:
        cli = client.cli()
        runner = CliRunner()

        # Test full address output
        result = runner.invoke(cli, ["address"])
        assert result.exit_code == 0
        assert result.output.strip() == "[::1]:8080"

        # Test --host option
        result = runner.invoke(cli, ["address", "--host"])
        assert result.exit_code == 0
        assert result.output.strip() == "::1"

        # Test --port option
        result = runner.invoke(cli, ["address", "--port"])
        assert result.exit_code == 0
        assert result.output.strip() == "8080"


def test_tcp_network_cli_complex_ipv6_address_command():
    """Test TCP network CLI with complex IPv6 address."""
    with serve(TcpNetwork(host="2001:db8::1", port=443, enable_address=True)) as client:
        cli = client.cli()
        runner = CliRunner()

        # Test full address output
        result = runner.invoke(cli, ["address"])
        assert result.exit_code == 0
        assert result.output.strip() == "[2001:db8::1]:443"

        # Test --host option
        result = runner.invoke(cli, ["address", "--host"])
        assert result.exit_code == 0
        assert result.output.strip() == "2001:db8::1"

        # Test --port option
        result = runner.invoke(cli, ["address", "--port"])
        assert result.exit_code == 0
        assert result.output.strip() == "443"


def test_tcp_network_cli_hostname_resolution():
    """Test TCP network CLI with hostname resolution."""
    with serve(TcpNetwork(host="localhost", port=8080, enable_address=True)) as client:
        cli = client.cli()
        runner = CliRunner()

        # Test full address output (should resolve hostname)
        result = runner.invoke(cli, ["address"])
        assert result.exit_code == 0
        assert result.output.strip() in ["127.0.0.1:8080", "[::1]:8080"]

        # Test --host option (should resolve hostname)
        result = runner.invoke(cli, ["address", "--host"])
        assert result.exit_code == 0
        assert result.output.strip() in ["127.0.0.1", "::1"]


def test_tcp_network_cli_address_disabled():
    """Test TCP network CLI when address is disabled."""
    with serve(TcpNetwork(host="127.0.0.1", port=8080, enable_address=False)) as client:
        cli = client.cli()
        runner = CliRunner()

        # Test address command should fail
        result = runner.invoke(cli, ["address"])
        assert result.exit_code != 0
        assert "enable_address mode is not true" in result.output
