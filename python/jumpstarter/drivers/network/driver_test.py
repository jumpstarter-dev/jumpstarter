import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from shutil import which
from tempfile import TemporaryDirectory

import anyio
import pytest

from jumpstarter.common.utils import serve
from jumpstarter.drivers.network.driver import EchoNetwork, TcpNetwork, UdpNetwork, UnixNetwork


def test_echo_network():
    with serve(EchoNetwork(name="echo")) as client:
        with client.connect() as stream:
            stream.send(b"hello")
            assert stream.receive() == b"hello"


def echo_handler(s):
    conn, _ = s.accept()
    while True:
        data = conn.recv(1024)
        if not data:
            break
        conn.sendall(data)


def test_tcp_network():
    with serve(TcpNetwork(name="tcp", host="127.0.0.1", port=9001)) as client:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 9001))
            s.listen(1)

            with ThreadPoolExecutor() as pool:
                pool.submit(echo_handler, s)

                with client.connect() as stream:
                    stream.send(b"hello")
                    assert stream.receive() == b"hello"


def test_tcp_network_portforward():
    with serve(TcpNetwork(name="tcp", host="127.0.0.1", port=8001)) as client:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 8001))
            s.listen(1)

            with ThreadPoolExecutor() as pool:
                pool.submit(echo_handler, s)

                async def create_forwarder():
                    return await anyio.create_tcp_listener(local_host="127.0.0.1", local_port=8002, reuse_port=True)

                forwarder = client.portal.call(create_forwarder)

                with client.portforward(forwarder):
                    stream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    stream.connect(("127.0.0.1", 8002))
                    stream.send(b"hello")
                    assert stream.recv(5) == b"hello"

                client.portal.call(forwarder.aclose)


def test_udp_network():
    with serve(
        UdpNetwork(
            name="udp",
            host="127.0.0.1",
            port=8001,
        )
    ) as client:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", 8001))

            with client.connect() as stream:
                stream.send(b"hello")
                assert s.recv(5) == b"hello"


def test_unix_network():
    with TemporaryDirectory() as tempdir:
        socketpath = Path(tempdir) / "socket"
        with serve(
            UnixNetwork(
                name="unix",
                path=socketpath,
            )
        ) as client:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.bind(str(socketpath))
                s.listen(1)

                with ThreadPoolExecutor() as pool:
                    pool.submit(echo_handler, s)

                    with client.connect() as stream:
                        stream.send(b"hello")
                        assert stream.receive() == b"hello"


@pytest.mark.skipif(which("iperf3") is None, reason="iperf3 not available")
def test_tcp_network_performance():
    with serve(
        TcpNetwork(
            name="iperf3",
            host="127.0.0.1",
            port=5201,
        )
    ) as client:

        async def create_listener():
            return await anyio.create_tcp_listener(local_port=8001, reuse_port=True)

        listener = client.portal.call(create_listener)

        server = subprocess.Popen(
            ["iperf3", "-s"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        with client.portforward(listener):
            subprocess.run(
                [
                    "iperf3",
                    "-c",
                    "127.0.0.1",
                    "-p",
                    "8001",
                    "-t",
                    "1",
                    "--bidir",
                ],
                stdout=sys.stdout,
                stderr=sys.stderr,
            )

        server.terminate()
