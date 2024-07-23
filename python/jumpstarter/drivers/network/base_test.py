import shutil
import subprocess
import sys

import anyio
import pytest

from jumpstarter.common.grpc import serve
from jumpstarter.drivers.network import EchoNetwork, TcpNetwork

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


@pytest.mark.skipif(shutil.which("iperf3") is None, reason="iperf3 not available")
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
