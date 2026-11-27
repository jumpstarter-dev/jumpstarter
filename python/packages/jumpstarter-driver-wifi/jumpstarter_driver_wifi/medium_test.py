import socket
import time

import anyio
import pytest
from anyio import to_thread

from .medium import WifiMedium
from .wmediumd_agent import frame_record
from .wmediumd_agent_test import BROADCAST, HERE, THERE, frame, read_record
from jumpstarter.common.utils import serve
from jumpstarter.driver import Port


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_frame_port_is_declared_as_a_connects_port():
    wifi = WifiMedium()
    (port,) = wifi.ports
    assert (port.name, port.direction, port.protocol) == ("hwsim", "connects", "ieee80211-frames")
    assert port.forwardable
    assert wifi.port_address(port) == ("127.0.0.1", 7310)
    assert [report.name for report in wifi.report().ports] == ["hwsim"]


def test_the_port_may_be_renamed_but_is_always_the_bridges_own():
    renamed = WifiMedium(ports=[Port(name="wifi-frames", direction="connects", protocol="ieee80211-frames")])
    assert renamed.ports[0].name == "wifi-frames"
    with pytest.raises(ValueError, match="own bridge"):
        WifiMedium(ports=[Port(name="hwsim", direction="connects", listen="127.0.0.1:7310")])
    with pytest.raises(ValueError, match="own bridge"):
        WifiMedium(ports=[Port(name="hwsim", direction="provides")])


def test_the_bridge_joins_the_medium_and_its_frame_port_carries_frames(medium, monkeypatch):
    monkeypatch.delenv("JUMPSTARTER_LAUNCHER_SOCKET", raising=False)
    port = free_port()
    with serve(WifiMedium(api_socket=medium.path, port=port, autostart=False)) as client:
        assert client.status() == {"running": False}
        status = client.wait_until_attached(timeout=10)
        assert status["running"] and status["attached"] and not status["peer"]
        client.start()  # (twice does nothing)
        (declared,) = client.ports()
        assert (declared["name"], declared["direction"], declared["protocol"]) == (
            "hwsim", "connects", "ieee80211-frames",
        )  # fmt: skip

        with socket.create_connection(("127.0.0.1", port), timeout=5) as frames:
            time.sleep(0.3)
            probe = frame(BROADCAST, HERE)
            assert medium.deliver(probe, freq=2442)
            assert read_record(frames) == (2442, probe)

            beacon = frame(BROADCAST, THERE)
            frames.sendall(frame_record(2442, beacon))
            assert medium.wait_for(lambda: medium.transmitted)
            assert medium.transmitted[0]["frame"] == beacon

        client.stop()
        assert client.status() == {"running": False}


@pytest.mark.anyio
async def test_a_forwarded_connection_takes_the_frame_port_only_once_it_is_used(medium, monkeypatch):
    monkeypatch.delenv("JUMPSTARTER_LAUNCHER_SOCKET", raising=False)
    wifi = WifiMedium(api_socket=medium.path, port=free_port(), retry=0.05)
    try:
        async with wifi.connect_port(wifi.ports[0]) as frames:
            # The exporter holds the local side while the far end is not there yet: the bridge is not even
            # started, and nothing fails. The first read waits for the bridge.
            received: list[bytes] = []

            async def read() -> None:
                received.append(await frames.receive())

            async with anyio.create_task_group() as tasks:
                tasks.start_soon(read)
                await anyio.sleep(0.2)
                assert not received
                await to_thread.run_sync(wifi.start)
                with anyio.fail_after(10):
                    while not (wifi.status().get("attached") and wifi.status().get("peer")):
                        await anyio.sleep(0.05)
                probe = frame(BROADCAST, HERE)
                assert await to_thread.run_sync(medium.deliver, probe)
            assert received == [frame_record(2437, probe)]
    finally:
        wifi.close()


def test_the_bridge_starts_with_the_session_and_stops_with_it(medium, monkeypatch):
    monkeypatch.delenv("JUMPSTARTER_LAUNCHER_SOCKET", raising=False)
    wifi = WifiMedium(api_socket=medium.path, port=free_port())
    with serve(wifi) as client:
        # nobody called start
        deadline = time.monotonic() + 10
        while not client.status().get("attached"):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        process = wifi._process
        assert process is not None and process.poll() is None
    assert process.poll() is not None and wifi.status() == {"running": False}


def test_through_a_launcher_the_bridge_is_handed_over_as_source(tmp_path):
    driver = WifiMedium(launcher_socket=str(tmp_path / "launcher.sock"))
    argv = driver._argv()
    assert argv[:5] == [str(tmp_path / "jumpstarter-exec"), "exec", "--socket", str(tmp_path / "launcher.sock"), "--"]
    assert argv[5:7] == ["python3", "-c"] and "class Bridge" in argv[7]
    assert argv[8:10] == ["--api-socket", "/tmp/cf_env_*/env-*/wmediumd_api_server"]


def test_the_kernels_own_radios_need_no_wmediumd(monkeypatch):
    monkeypatch.delenv("JUMPSTARTER_LAUNCHER_SOCKET", raising=False)
    argv = WifiMedium(medium="hwsim", port=7320, ignore=[])._argv()
    assert argv[2:] == ["--hwsim", "--listen", "127.0.0.1:7320", "--ignore", ""]
    assert "--api-socket" in WifiMedium()._argv()


@pytest.mark.anyio
async def test_a_bridge_inside_the_device_is_not_run_here_and_is_known_by_its_path():
    """A QEMU guest runs the bridge itself: the driver has its frame port, as a forward of the guest gives it."""
    port = free_port()
    wifi = WifiMedium(external=True, port=port, retry=0.02)
    wifi.start()
    assert wifi._process is None
    assert wifi.status() == {"running": True, "external": True, "attached": False, "peer": False}

    guests: list = []

    async def connected(guest) -> None:
        guests.append(await guest.receive())
        await anyio.sleep_forever()

    async with (
        await anyio.create_tcp_listener(local_host="127.0.0.1", local_port=port) as bridge,
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(bridge.serve, connected)
        async with wifi.connect_port(wifi.ports[0]) as frames:
            assert not wifi.status()["peer"]  # (a path that nothing crossed yet is not open)
            await frames.send(frame_record(5180, frame(BROADCAST, THERE)))
            with anyio.fail_after(5):
                while not guests:
                    await anyio.sleep(0.01)
            assert guests == [frame_record(5180, frame(BROADCAST, THERE))]
            assert wifi.status() == {"running": True, "external": True, "attached": True, "peer": True}
        assert not wifi.status()["peer"]
        tasks.cancel_scope.cancel()
    wifi.stop()
