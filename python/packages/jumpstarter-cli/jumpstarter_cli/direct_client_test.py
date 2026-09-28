"""Exercise installed clients over real TCP and local socket sessions."""

import os
import subprocess
import sys
from contextlib import ExitStack

import pytest
from anyio import fail_after
from anyio.from_thread import start_blocking_portal
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_network.driver import EchoNetwork
from jumpstarter_driver_power.driver import MockPower

from jumpstarter.client import client_from_path
from jumpstarter.common import ExporterStatus
from jumpstarter.exporter import Session

ALLOWED_CLIENTS = [
    "jumpstarter_driver_composite.client.CompositeClient",
    "jumpstarter_driver_network.client.NetworkClient",
    "jumpstarter_driver_power.client.PowerClient",
]


@pytest.fixture(params=["tcp", "unix"])
def direct_session(request):
    driver = Composite(children={"power": MockPower(), "network": EchoNetwork()})
    with start_blocking_portal() as portal, Session(root_device=driver) as session:
        session.update_status(ExporterStatus.LEASE_READY)
        if request.param == "tcp":
            with portal.wrap_async_context_manager(session.serve_tcp_async("127.0.0.1", 0)) as port:
                yield f"127.0.0.1:{port}", portal
        else:
            with portal.wrap_async_context_manager(session.serve_unix_async()) as path:
                yield str(path), portal


def test_direct_sdk_discovers_clients_and_calls_drivers(direct_session):
    address, portal = direct_session
    with ExitStack() as stack, portal.wrap_async_context_manager(client_from_path(
        address, portal, stack, allow=ALLOWED_CLIENTS, unsafe=False, insecure=True,
    )) as client:
        async def exercise():
            with fail_after(10):
                await client.power.call_async("on")
                assert [reading async for reading in client.power.streamingcall_async("read")]
                await client.power.call_async("off")
                payload = b"\x00\xff\x1a\r\nbinary network data"
                async with client.network.stream_async("connect") as stream:
                    await stream.send(payload)
                    assert await stream.receive() == payload

        portal.call(exercise)


def test_driver_cli_subprocesses_share_direct_endpoint(direct_session, tmp_path):
    address, _ = direct_session
    env = os.environ | {
        "JUMPSTARTER_HOST": address,
        "JMP_DRIVERS_ALLOW": ",".join(ALLOWED_CLIENTS),
        "JMP_GRPC_INSECURE": "1",
        "JMP_CLIENT_CONFIG_HOME": str(tmp_path),
        "PYTHONUTF8": "1",
    }
    for args in (("--help",), ("power", "on"), ("power", "read"), ("power", "off")):
        result = subprocess.run(
            [sys.executable, "-m", "jumpstarter_cli.j", *args],
            env=env, capture_output=True, text=True, timeout=20, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        if args == ("--help",):
            assert "power" in result.stdout
            assert "network" in result.stdout
        elif args == ("power", "read"):
            assert "voltage=" in result.stdout
