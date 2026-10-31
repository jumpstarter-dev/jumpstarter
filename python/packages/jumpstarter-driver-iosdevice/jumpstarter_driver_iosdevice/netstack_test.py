import ipaddress
import struct

import anyio
import pytest

from .netstack import (
    ACK,
    ETHERTYPE_IPV6,
    FIN,
    ICMP_NEIGHBOR_ADVERT,
    ICMP_NEIGHBOR_SOLICIT,
    PROTO_ICMPV6,
    PROTO_TCP,
    PSH,
    RST,
    SYN,
    Stack,
    _checksum,
    _ipv6,
    _pseudo,
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


def tcp(sport, dport, seq, ack, flags, payload=b"", options=b""):
    header = struct.pack("!HHIIBBHHH", sport, dport, seq, ack, ((20 + len(options)) // 4) << 4, flags, 65535, 0, 0)
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
