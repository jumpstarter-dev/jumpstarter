import os
import socket
import subprocess
import sys
from shutil import which
from unittest.mock import AsyncMock, patch

import pytest
from anyio.from_thread import start_blocking_portal

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
        client = WebsocketNetwork(url="ws://localhost/something")
        async with client.connect():
            m.assert_called_once_with("ws://localhost/something")
