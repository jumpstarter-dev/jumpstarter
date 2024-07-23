import subprocess
import sys
from pathlib import Path
from shutil import which
from tempfile import TemporaryDirectory

import anyio
import pytest

from jumpstarter.common.grpc import serve
from jumpstarter.drivers.network import EchoNetwork, TcpNetwork, UdpNetwork, UnixNetwork

pytestmark = pytest.mark.anyio


async def test_echo_network():
    async with serve(
        EchoNetwork(
            labels={"jumpstarter.dev/name": "echo"},
        )
    ) as client:
        async with client.connect() as stream:
            await stream.send(b"hello")
            assert await stream.receive() == b"hello"


async def test_udp_network():
    async with await anyio.create_udp_socket(
        local_host="127.0.0.1",
        local_port=8001,
    ) as server:
        async with serve(
            UdpNetwork(
                labels={"jumpstarter.dev/name": "udp"},
                host="127.0.0.1",
                port=8001,
            )
        ) as client:
            async with client.connect() as stream:
                await stream.send(b"hello")
        # TODO: fix udp stream object type
        assert (await server.receive())[0] == b"hello"


async def echo_handler(client):
    async with client:
        async for v in client:
            await client.send(v)


async def test_unix_network():
    with TemporaryDirectory() as tempdir:
        socketpath = Path(tempdir) / "socket"

        listener = await anyio.create_unix_listener(socketpath)

        async with anyio.create_task_group() as tg:
            tg.start_soon(listener.serve, echo_handler)

            async with serve(
                UnixNetwork(
                    labels={"jumpstarter.dev/name": "unix"},
                    path=socketpath,
                )
            ) as client:
                async with client.connect() as stream:
                    await stream.send(b"hello")
                    assert await stream.receive() == b"hello"

            tg.cancel_scope.cancel()


@pytest.mark.skipif(which("iperf3") is None, reason="iperf3 not available")
async def test_tcp_network_performance():
    listener = await anyio.create_tcp_listener(local_port=8001)

    async with serve(
        TcpNetwork(
            labels={"jumpstarter.dev/name": "iperf3"},
            host="127.0.0.1",
            port=5201,
        )
    ) as client:
        async with await anyio.open_process(
            ["iperf3", "-s"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ) as server:
            async with client.portforward(listener):
                await anyio.run_process(
                    [
                        "iperf3",
                        "-c",
                        "127.0.0.1",
                        "-p",
                        "8001",
                        "-t",
                        "1",
                    ],
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                )
            server.terminate()
