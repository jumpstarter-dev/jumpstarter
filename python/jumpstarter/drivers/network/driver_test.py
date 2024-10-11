import socket
import subprocess
import sys
from shutil import which

import pytest
from anyio.from_thread import start_blocking_portal

from jumpstarter.client.adapters import PortforwardAdapter
from jumpstarter.common import TemporaryTcpListener, TemporaryUnixListener
from jumpstarter.common.utils import serve
from jumpstarter.drivers.network.driver import EchoNetwork, TcpNetwork, UdpNetwork, UnixNetwork


def test_echo_network():
    with serve(EchoNetwork()) as client:
        with client.stream() as stream:
            stream.send(b"hello")
            assert stream.receive() == b"hello"


async def echo_handler(stream):
    async with stream:
        while True:
            try:
                await stream.send(await stream.receive())
            except Exception:
                pass


def test_tcp_network():
    with start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(TemporaryTcpListener(echo_handler, local_host="127.0.0.1")) as addr:
            with serve(TcpNetwork(host=addr[0], port=addr[1])) as client:
                with client.stream() as stream:
                    stream.send(b"hello")
                    assert stream.receive() == b"hello"


def test_tcp_network_portforward():
    with start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(TemporaryTcpListener(echo_handler, local_host="127.0.0.1")) as inner:
            with serve(TcpNetwork(host=inner[0], port=inner[1])) as client:
                with PortforwardAdapter(client=client) as addr:
                    stream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    stream.connect(addr)
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

        with PortforwardAdapter(client=client) as addr:
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
