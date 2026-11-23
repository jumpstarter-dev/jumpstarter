import socket
import time

from .wmediumd import WmediumdWifi
from .wmediumd_agent import frame_record
from .wmediumd_agent_test import BROADCAST, HERE, THERE, frame, read_record
from jumpstarter.common.utils import serve


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_bridge_joins_the_medium_and_its_frame_port_carries_frames(medium, monkeypatch):
    monkeypatch.delenv("JUMPSTARTER_LAUNCHER_SOCKET", raising=False)
    with serve(WmediumdWifi(api_socket=medium.path, port=free_port())) as client:
        assert client.status() == {"running": False}
        status = client.wait_until_attached(timeout=10)
        assert status["running"] and status["attached"] and not status["peer"]
        client.start()  # (twice does nothing)
        (port,) = client.ports()
        assert (port.name, port.direction, port.protocol) == ("hwsim", "connects", "ieee80211-frames")
        assert port.child == "frames"

        with client.frames.stream() as frames:
            time.sleep(0.3)
            probe = frame(BROADCAST, HERE)
            assert medium.deliver(probe, freq=2442)
            received = b""
            while len(received) < len(frame_record(2442, probe)):
                received += frames.receive()
            assert received == frame_record(2442, probe)

            beacon = frame(BROADCAST, THERE)
            frames.send(frame_record(2442, beacon))
            assert medium.wait_for(lambda: medium.transmitted)
            assert medium.transmitted[0]["frame"] == beacon

        client.stop()
        assert client.status() == {"running": False}


def test_the_frame_port_is_reachable_as_a_plain_tcp_port(medium, monkeypatch):
    monkeypatch.delenv("JUMPSTARTER_LAUNCHER_SOCKET", raising=False)
    port = free_port()
    with serve(WmediumdWifi(api_socket=medium.path, port=port)) as client:
        client.wait_until_attached(timeout=10)
        with socket.create_connection(("127.0.0.1", port), timeout=5) as peer:
            time.sleep(0.3)
            probe = frame(BROADCAST, HERE)
            assert medium.deliver(probe)
            assert read_record(peer) == (2437, probe)


def test_through_a_launcher_the_bridge_is_handed_over_as_source(tmp_path):
    driver = WmediumdWifi(launcher_socket=str(tmp_path / "launcher.sock"))
    argv = driver._argv()
    assert argv[:5] == [str(tmp_path / "jumpstarter-exec"), "exec", "--socket", str(tmp_path / "launcher.sock"), "--"]
    assert argv[5:7] == ["python3", "-c"] and "class Bridge" in argv[7]
    assert argv[8:10] == ["--api-socket", "/tmp/cf_env_*/env-*/wmediumd_api_server"]
