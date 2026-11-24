import json
import socket
import struct
import subprocess
import sys
import time

import pytest

from . import wmediumd_agent
from .wmediumd import AGENT
from .wmediumd_agent import (
    ATTR_ADDR_TRANSMITTER,
    ATTR_COOKIE,
    ATTR_FLAGS,
    ATTR_FRAME,
    ATTR_FREQ,
    ATTR_TX_INFO,
    frame_record,
)

HERE = bytes.fromhex("7a1610e5e475")  # a station on this side (a phone's randomized address)
THERE = bytes.fromhex("aa744a492992")  # a station on the far side (a head unit's access point)
BROADCAST = b"\xff" * 6
OWN_ACCESS_POINT = bytes.fromhex("020000000000")
FACTORY = bytes.fromhex("0215b2000000")


def frame(receiver: bytes, sender: bytes, body: bytes = b"hello") -> bytes:
    """A data frame: frame control and duration, receiver, sender, BSSID, sequence, then the body."""
    return b"\x08\x00\x00\x00" + receiver + sender + receiver + b"\x00\x00" + body


@pytest.fixture
def bridge(medium):
    process = subprocess.Popen(
        [
            sys.executable, str(AGENT),
            "--api-socket", medium.path,
            "--listen", "127.0.0.1:0",
            "--ignore", "02:00:00:00,02:15:b2",
        ],
        stdout=subprocess.PIPE, text=True,
    )  # fmt: skip
    try:
        assert process.stdout is not None
        port = json.loads(process.stdout.readline())["port"]
        assert medium.registered.wait(5) and medium.hears_all.wait(5)
        yield port
    finally:
        process.terminate()
        process.wait(5)


@pytest.fixture
def peer(bridge):
    with socket.create_connection(("127.0.0.1", bridge), timeout=5) as connection:
        time.sleep(0.2)  # (until the bridge has taken the connection)
        yield connection


def read_record(connection: socket.socket) -> tuple[int, bytes]:
    def exactly(size):
        data = b""
        while len(data) < size:
            chunk = connection.recv(size - len(data))
            assert chunk, "the bridge closed the frame port"
            data += chunk
        return data

    payload = exactly(struct.unpack(">I", exactly(4))[0])
    return struct.unpack(">I", payload[:4])[0], payload[4:]


def test_a_broadcast_frame_heard_on_the_medium_comes_out_of_the_port(medium, peer):
    probe = frame(BROADCAST, HERE)
    assert medium.deliver(probe, freq=2442)
    assert read_record(peer) == (2442, probe)


def test_every_frame_is_acknowledged_even_with_nobody_at_the_port(medium, bridge):
    for number in range(20):
        assert medium.deliver(frame(BROADCAST, HERE, bytes([number])))


def test_a_frame_from_the_far_side_is_sent_into_the_medium_as_the_bridge_station(medium, peer):
    beacon = frame(BROADCAST, THERE)
    peer.sendall(frame_record(5180, beacon))
    assert medium.wait_for(lambda: medium.transmitted)
    sent = medium.transmitted[0]
    assert sent["frame"] == beacon and sent["freq"] == 5180
    assert sent["station"] == bytes.fromhex("0215b3000000")
    # the medium reads all of these without checking; a frame without one would take the medium down
    assert {ATTR_ADDR_TRANSMITTER, ATTR_FRAME, ATTR_FLAGS, ATTR_TX_INFO, ATTR_COOKIE, ATTR_FREQ} <= sent["attributes"]


def test_the_far_side_station_is_claimed_once_so_that_frames_to_it_reach_the_bridge(medium, peer):
    peer.sendall(frame_record(2442, frame(BROADCAST, THERE, b"1")) + frame_record(2442, frame(BROADCAST, THERE, b"2")))
    assert medium.wait_for(lambda: len(medium.transmitted) == 2)
    assert medium.claimed == [THERE]
    reply = frame(THERE, HERE)
    assert medium.deliver(reply)
    assert read_record(peer) == (2437, reply)


def test_what_the_far_side_sent_does_not_go_back_to_it(medium, peer):
    peer.sendall(frame_record(2442, frame(BROADCAST, THERE)))
    assert medium.wait_for(lambda: medium.transmitted)
    assert medium.deliver(frame(BROADCAST, THERE))  # the medium gives the bridge its own frame back
    marker = frame(BROADCAST, HERE, b"marker")
    assert medium.deliver(marker)
    assert read_record(peer)[1] == marker


def test_frames_between_stations_of_this_side_stay_on_this_side(medium, peer):
    assert medium.deliver(frame(bytes.fromhex("7a1610e5e476"), HERE))
    marker = frame(BROADCAST, HERE, b"marker")
    assert medium.deliver(marker)
    assert read_record(peer)[1] == marker


def test_the_devices_own_access_point_and_factory_addresses_are_not_passed_on(medium, peer):
    assert medium.deliver(frame(BROADCAST, OWN_ACCESS_POINT))
    assert medium.deliver(frame(BROADCAST, FACTORY))
    marker = frame(BROADCAST, HERE, b"marker")
    assert medium.deliver(marker)
    assert read_record(peer)[1] == marker


def test_the_copy_the_medium_makes_for_each_listener_goes_out_once(medium, peer):
    probe = frame(BROADCAST, HERE)
    assert medium.deliver(probe) and medium.deliver(probe)
    marker = frame(BROADCAST, HERE, b"marker")
    assert medium.deliver(marker)
    assert read_record(peer)[1] == probe
    assert read_record(peer)[1] == marker


def test_a_peer_that_does_not_read_does_not_stall_the_medium(medium, peer):
    big = frame(BROADCAST, HERE, b"x" * 1400)
    for number in range(6000):  # more than the port will hold for a peer that never reads
        assert medium.deliver(big + struct.pack(">I", number), timeout=2), f"frame {number} was not acknowledged"


def test_a_new_connection_replaces_the_old_one(medium, bridge):
    first = socket.create_connection(("127.0.0.1", bridge), timeout=5)
    second = socket.create_connection(("127.0.0.1", bridge), timeout=5)
    time.sleep(0.3)
    probe = frame(BROADCAST, HERE)
    assert medium.deliver(probe)
    assert read_record(second) == (2437, probe)
    assert first.recv(1) == b""  # closed by the bridge
    first.close()
    second.close()


def test_a_record_that_is_not_a_frame_closes_the_connection(medium, peer):
    peer.sendall(struct.pack(">I", 1 << 30))
    assert peer.recv(1) == b""


def test_a_frame_that_waited_too_long_for_a_peer_is_not_sent_to_it(medium, monkeypatch):
    bridge = wmediumd_agent.Bridge(medium.path, ("127.0.0.1", 0), HERE, [])
    try:
        bridge.peer = socket.socket()  # (a peer that is there, and is not reading)
        clock = [1000.0]
        monkeypatch.setattr(wmediumd_agent.time, "monotonic", lambda: clock[0])
        bridge.heard(2437, frame(BROADCAST, HERE, b"old"))
        clock[0] += wmediumd_agent.MAX_AGE + 1
        bridge.heard(2437, frame(BROADCAST, HERE, b"new"))
        assert bridge.next_record()
        assert bytes(bridge.outgoing) == frame_record(2437, frame(BROADCAST, HERE, b"new"))
        assert bridge.stats["dropped"] == 1 and bridge.backlog == 0
    finally:
        bridge.peer.close()
        bridge.listener.close()
