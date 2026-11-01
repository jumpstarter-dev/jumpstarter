import errno
import ipaddress
import struct

import anyio
import pytest

from . import netstack
from .netstack import (
    ACK,
    ETHERTYPE_IPV6,
    FIN,
    ICMP_ECHO_REPLY,
    ICMP_ECHO_REQUEST,
    ICMP_NEIGHBOR_ADVERT,
    ICMP_NEIGHBOR_SOLICIT,
    PROTO_ICMPV6,
    PROTO_TCP,
    PSH,
    RST,
    SYN,
    Bpf,
    Stack,
    _checksum,
    _ipv6,
    _pseudo,
    interface_mac,
)

pytestmark = pytest.mark.anyio

DEVICE = ipaddress.IPv6Address("fd6a:7374:6a73::2").packed
MAC = ipaddress.IPv6Address("fd6a:7374:6a73::1").packed
DEVICE_ETHER = bytes.fromhex("020000000002")
MAC_ETHER = bytes.fromhex("020000000001")


class FakeBpf:
    def __init__(self):
        self.sent = []

    def write(self, frame):
        self.sent.append(frame)

    def close(self):
        pass


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def stack():
    stack = Stack.__new__(Stack)
    stack.interface = "feth1"
    stack.address = DEVICE
    stack.mac = DEVICE_ETHER
    stack.mss = 1400
    stack._bpf = FakeBpf()  # ty: ignore[invalid-assignment]
    stack._connections = {}
    stack._listeners = {}
    return stack


def frame(proto, payload, src=MAC, dst=DEVICE):
    return DEVICE_ETHER + MAC_ETHER + struct.pack("!H", ETHERTYPE_IPV6) + _ipv6(src, dst, proto, payload)


def tcp(sport, dport, seq, ack, flags, payload=b"", options=b"", window=65535):
    header = struct.pack("!HHIIBBHHH", sport, dport, seq, ack, ((20 + len(options)) // 4) << 4, flags, window, 0, 0)
    segment = header + options + payload
    checksum = _pseudo(MAC, DEVICE, PROTO_TCP, segment)
    return segment[:16] + struct.pack("!H", checksum) + segment[18:]


def sent_ip(stack):
    data = stack._bpf.sent.pop(0)
    assert data[:6] == MAC_ETHER and data[6:12] == DEVICE_ETHER
    ip = data[14:]
    length, proto, hop_limit = struct.unpack_from("!HBB", ip, 4)
    return proto, hop_limit, ip[8:24], ip[24:40], ip[40 : 40 + length]


def sent_tcp(stack):
    proto, _hop, src, dst, segment = sent_ip(stack)
    assert proto == PROTO_TCP and src == DEVICE and dst == MAC
    assert _pseudo(src, dst, PROTO_TCP, segment) == 0
    _sport, _dport, seq, ack, offset, flags = struct.unpack_from("!HHIIBB", segment)
    return seq, ack, flags, segment[(offset >> 4) * 4 :], segment[20 : (offset >> 4) * 4]


async def test_answers_neighbor_solicitation_for_its_address(stack):
    solicit = struct.pack("!BBHI", ICMP_NEIGHBOR_SOLICIT, 0, 0, 0) + DEVICE + bytes((1, 1)) + MAC_ETHER
    solicit = solicit[:2] + struct.pack("!H", _pseudo(MAC, DEVICE, PROTO_ICMPV6, solicit)) + solicit[4:]
    stack._receive_frame(frame(PROTO_ICMPV6, solicit))

    proto, hop_limit, src, dst, advert = sent_ip(stack)
    assert (proto, hop_limit, src, dst) == (PROTO_ICMPV6, 255, DEVICE, MAC)
    assert advert[0] == ICMP_NEIGHBOR_ADVERT and advert[8:24] == DEVICE
    assert advert[24:26] == bytes((2, 1)) and advert[26:32] == DEVICE_ETHER
    assert _pseudo(DEVICE, MAC, PROTO_ICMPV6, advert) == 0


async def test_ignores_solicitations_for_other_addresses(stack):
    other = ipaddress.IPv6Address("fd6a:7374:6a73::9").packed
    solicit = struct.pack("!BBHI", ICMP_NEIGHBOR_SOLICIT, 0, 0, 0) + other
    stack._receive_frame(frame(PROTO_ICMPV6, solicit, dst=other))
    assert stack._bpf.sent == []


async def test_tcp_connection_exchanges_data_and_closes(stack):
    accepted = stack.listen(49152)
    stack._receive_frame(frame(PROTO_TCP, tcp(50000, 49152, 1000, 0, SYN, options=struct.pack("!BBH", 2, 4, 1200))))
    connection = accepted.get_nowait()
    isn, ack, flags, _payload, options = sent_tcp(stack)
    assert flags == SYN | ACK and ack == 1001 and options == struct.pack("!BBH", 2, 4, 1400)
    assert connection.mss == 1200

    stack._receive_frame(frame(PROTO_TCP, tcp(50000, 49152, 1001, isn + 1, ACK | PSH, b"hello")))
    assert connection.established.is_set()
    assert await connection.receive() == b"hello"
    _seq, ack, flags, _payload, _options = sent_tcp(stack)
    assert ack == 1006 and flags == ACK

    await connection.send(b"x" * 3000)
    segments = [sent_tcp(stack) for _ in range(3)]
    assert [len(segment[3]) for segment in segments] == [1200, 1200, 600]
    assert [segment[0] for segment in segments] == [isn + 1, isn + 1201, isn + 2401]

    stack._receive_frame(frame(PROTO_TCP, tcp(50000, 49152, 1006, isn + 3001, ACK | FIN)))
    assert connection._unacked == bytearray()
    with pytest.raises(anyio.EndOfStream):
        await connection.receive()
    _seq, ack, _flags, _payload, _options = sent_tcp(stack)
    assert ack == 1007

    await connection.aclose()
    seq, _ack, flags, _payload, _options = sent_tcp(stack)
    assert flags == ACK | FIN and seq == isn + 3001
    stack._receive_frame(frame(PROTO_TCP, tcp(50000, 49152, 1007, isn + 3002, ACK)))
    assert stack._connections == {}


async def test_out_of_order_data_is_not_delivered(stack):
    accepted = stack.listen(49152)
    stack._receive_frame(frame(PROTO_TCP, tcp(50000, 49152, 1000, 0, SYN)))
    connection = accepted.get_nowait()
    isn = sent_tcp(stack)[0]
    stack._receive_frame(frame(PROTO_TCP, tcp(50000, 49152, 1010, isn + 1, ACK, b"later")))
    _seq, ack, _flags, _payload, _options = sent_tcp(stack)
    assert ack == 1001 and not connection._received


async def test_unacknowledged_data_is_resent(stack):
    accepted = stack.listen(49152)
    stack._receive_frame(frame(PROTO_TCP, tcp(50000, 49152, 1000, 0, SYN)))
    connection = accepted.get_nowait()
    isn = sent_tcp(stack)[0]
    await connection.send(b"data")
    sent_tcp(stack)
    connection.on_timer(connection._deadline + 0.01)
    seq, _ack, _flags, payload, _options = sent_tcp(stack)
    assert (seq, payload) == (isn + 1, b"data")


async def test_unknown_connection_is_reset(stack):
    stack._receive_frame(frame(PROTO_TCP, tcp(50000, 49152, 1000, 77, ACK, b"stray")))
    seq, ack, flags, _payload, _options = sent_tcp(stack)
    assert flags & RST and seq == 77 and ack == 1005


def test_checksum_of_checksummed_data_is_zero():
    data = b"\x12\x34\x56"
    folded = _checksum(data + b"\x00")
    assert _checksum(data + b"\x00" + struct.pack("!H", folded)) == 0


def _accept(stack, port=49152):
    accepted = stack.listen(port)
    stack._receive_frame(frame(PROTO_TCP, tcp(50000, port, 1000, 0, SYN)))
    connection = accepted.get_nowait()
    return connection, sent_tcp(stack)[0]


async def test_answers_echo_requests(stack):
    request = bytes((ICMP_ECHO_REQUEST, 0, 0, 0)) + b"\x00\x01\x00\x02ping"
    stack._receive_frame(frame(PROTO_ICMPV6, request))
    proto, _hop, src, dst, reply = sent_ip(stack)
    assert (proto, src, dst) == (PROTO_ICMPV6, DEVICE, MAC)
    assert reply[0] == ICMP_ECHO_REPLY and reply[4:] == request[4:]
    assert _pseudo(DEVICE, MAC, PROTO_ICMPV6, reply) == 0


async def test_ignores_other_traffic(stack):
    stack._receive_frame(DEVICE_ETHER + MAC_ETHER + b"\x08\x00" + bytes(60))
    stack._receive_frame(frame(PROTO_TCP, tcp(50000, 49152, 1, 0, SYN), dst=MAC))
    stack._receive_frame(frame(PROTO_TCP, tcp(50000, 49152, 1, 0, RST)))
    stack._receive_frames([frame(PROTO_TCP, b"\x00" * 8)])
    assert stack._bpf.sent == []


async def test_syn_options_are_parsed_past_padding(stack):
    accepted = stack.listen(49152)
    options = bytes((1, 3, 3, 7)) + struct.pack("!BBH", 2, 4, 900)
    stack._receive_frame(frame(PROTO_TCP, tcp(50000, 49152, 1000, 0, SYN, options=options)))
    assert accepted.get_nowait().mss == 900


async def test_reset_fails_the_connection(stack):
    connection, isn = _accept(stack)
    stack._receive_frame(frame(PROTO_TCP, tcp(50000, 49152, 1001, isn + 1, RST)))
    assert stack._connections == {}
    with pytest.raises(anyio.BrokenResourceError, match="reset"):
        await connection.receive()
    with pytest.raises(anyio.BrokenResourceError):
        await connection.send(b"late")


async def test_connection_gives_up_on_a_silent_peer(stack, monkeypatch):
    monkeypatch.setattr(netstack, "RETRANSMIT_LIMIT", 1)
    connection, _isn = _accept(stack)
    await connection.send_eof()
    sent_tcp(stack)
    connection.on_timer(connection._deadline - 1)
    assert stack._bpf.sent == []
    connection.on_timer(connection._deadline + 0.01)
    _seq, _ack, flags, _payload, _options = sent_tcp(stack)
    assert flags == ACK | FIN
    connection.on_timer(connection._deadline + 0.01)
    with pytest.raises(anyio.BrokenResourceError, match="stopped acknowledging"):
        await connection.receive()


async def test_receive_splits_large_segments(stack):
    connection, isn = _accept(stack)
    stack._receive_frame(frame(PROTO_TCP, tcp(50000, 49152, 1001, isn + 1, ACK | PSH, b"abcdef")))
    assert await connection.receive(4) == b"abcd"
    assert await connection.receive() == b"ef"
    assert connection.remote_address == (str(ipaddress.IPv6Address(MAC)), 50000)


async def test_send_waits_for_the_peer_window(stack):
    connection, isn = _accept(stack)
    stack._receive_frame(frame(PROTO_TCP, tcp(50000, 49152, 1001, isn + 1, ACK, window=0)))
    await connection.send(b"x" * 4 * connection.mss)
    assert stack._bpf.sent == []
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(connection.send, b"more")
        await anyio.wait_all_tasks_blocked()
        assert stack._bpf.sent == []
        stack._receive_frame(frame(PROTO_TCP, tcp(50000, 49152, 1001, isn + 1, ACK)))
    assert len(stack._bpf.sent) > 4
    assert connection._unsent == bytearray()


async def test_blocked_send_reports_a_failed_connection(stack):
    connection, isn = _accept(stack)
    stack._receive_frame(frame(PROTO_TCP, tcp(50000, 49152, 1001, isn + 1, ACK, window=0)))
    await connection.send(b"x" * 4 * connection.mss)
    async with anyio.create_task_group() as tasks:

        async def blocked():
            with pytest.raises(anyio.BrokenResourceError):
                await connection.send(b"more")

        tasks.start_soon(blocked)
        await anyio.wait_all_tasks_blocked()
        connection.fail(anyio.BrokenResourceError("gone"))


async def test_closed_connection_refuses_io(stack):
    connection, _isn = _accept(stack)
    await connection.aclose()
    await connection.aclose()
    with pytest.raises(anyio.ClosedResourceError):
        await connection.send(b"late")
    with pytest.raises(anyio.ClosedResourceError):
        await connection.receive()


async def test_send_errors_other_than_shutdown_are_logged(stack, caplog):
    def write(frame):
        raise OSError(errno.ENETDOWN, "network is down")

    stack._bpf.write = write
    stack.send_ipv6(MAC_ETHER, DEVICE, MAC, PROTO_TCP, b"")
    assert "cannot send on feth1" in caplog.text
    caplog.clear()

    def closed(frame):
        raise OSError(errno.EBADF, "bad file descriptor")

    stack._bpf.write = closed
    stack.send_ipv6(MAC_ETHER, DEVICE, MAC, PROTO_TCP, b"")
    assert caplog.text == ""


def _bpf_record(data):
    header = bytes(8) + struct.pack("=IIH", len(data), len(data), 18)
    record = header + data
    return record + bytes(-len(record) % 4)


def test_bpf_buffer_holds_aligned_frames():
    bpf = Bpf.__new__(Bpf)
    assert list(bpf.frames(_bpf_record(b"first") + _bpf_record(b"second frame"))) == [b"first", b"second frame"]


def test_bpf_opens_the_first_free_device(monkeypatch):
    opened, controls = [], []

    def open_device(path, flags):
        opened.append(path)
        if path == "/dev/bpf0":
            raise OSError(errno.EBUSY, "busy")
        return 42

    monkeypatch.setattr(netstack.os, "open", open_device)
    monkeypatch.setattr(netstack.fcntl, "ioctl", lambda fd, request, value: controls.append((fd, request)))
    monkeypatch.setattr(netstack.os, "read", lambda fd, size: b"frames")
    written = []
    monkeypatch.setattr(netstack.os, "write", lambda fd, data: written.append((fd, data)))
    monkeypatch.setattr(netstack.os, "close", lambda fd: written.append((fd, "closed")))
    bpf = Bpf("feth1")
    assert opened == ["/dev/bpf0", "/dev/bpf1"] and bpf.fd == 42
    assert controls[1] == (42, netstack.BIOCSETIF)
    assert bpf.read() == b"frames"
    bpf.write(b"frame")
    bpf.close()
    assert written == [(42, b"frame"), (42, "closed")]


def test_bpf_needs_the_setup_script(monkeypatch):
    def missing(path, flags):
        raise FileNotFoundError(path)

    monkeypatch.setattr(netstack.os, "open", missing)
    with pytest.raises(PermissionError, match="devicehub-link-macos.sh"):
        Bpf("feth1")

    def busy(path, flags):
        raise OSError(errno.EBUSY, "busy")

    monkeypatch.setattr(netstack.os, "open", busy)
    with pytest.raises(OSError, match="no BPF device is free"):
        Bpf("feth1")


def test_interface_mac_is_read_from_ifconfig(monkeypatch):
    output = "feth1: flags=8843\n\tether 02:00:00:00:00:02\n"
    monkeypatch.setattr(netstack.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": output})())
    assert interface_mac("feth1") == DEVICE_ETHER
    monkeypatch.setattr(netstack.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": "lo0: flags\n"})())
    with pytest.raises(OSError, match="no Ethernet address"):
        interface_mac("lo0")


class ThreadedBpf(FakeBpf):
    def __init__(self, interface, frames):
        super().__init__()
        self.buffers = [b"".join(_bpf_record(item) for item in frames), b""]
        self.frames = Bpf.frames.__get__(self)

    def read(self):
        if not self.buffers:
            raise OSError(errno.EBADF, "closed")
        return self.buffers.pop(0)


async def test_stack_reads_frames_on_a_thread_and_resets_on_exit(monkeypatch):
    solicit = struct.pack("!BBHI", ICMP_NEIGHBOR_SOLICIT, 0, 0, 0) + DEVICE
    bpfs = []

    def make_bpf(interface):
        bpfs.append(
            ThreadedBpf(interface, [frame(PROTO_ICMPV6, solicit), frame(PROTO_TCP, tcp(50000, 49152, 1000, 0, SYN))])
        )
        return bpfs[-1]

    monkeypatch.setattr(netstack, "Bpf", make_bpf)
    monkeypatch.setattr(netstack, "interface_mac", lambda name: DEVICE_ETHER)
    async with Stack("feth1", "fd6a:7374:6a73::2") as stack:
        accepted = stack.listen(49152)
        with anyio.fail_after(5):
            connection = await accepted.get()
        await connection.send(b"data")
        with anyio.fail_after(5):
            while len(bpfs[0].sent) < 4:
                await anyio.sleep(0.05)
    advert, _syn_ack, _data, *rest = bpfs[0].sent
    assert advert[54] == ICMP_NEIGHBOR_ADVERT
    assert rest and rest[-1][14 + 40 + 13] & RST
    with pytest.raises(anyio.ClosedResourceError):
        await connection.receive()
