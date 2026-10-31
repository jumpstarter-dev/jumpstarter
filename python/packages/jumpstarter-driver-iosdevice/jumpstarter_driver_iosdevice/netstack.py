"""A minimal IPv6 host on a macOS ``feth`` interface, reached through BPF.

macOS only keeps a network iOS device connected while the device answers
neighbor discovery on its link. A leased device is reached through Jumpstarter
instead, so the client answers for it: this module owns one IPv6 address on
``feth1`` (whose peer ``feth0`` carries the Mac's side of the link), replies to
neighbor solicitations and echo requests, and accepts TCP connections.

The link is a pair of virtual interfaces on one machine, so it neither loses
nor reorders frames in practice. TCP here is accordingly small: in-order
delivery, cumulative acknowledgements, a fixed window without scaling and a
timer that resends unacknowledged data.
"""

import asyncio
import fcntl
import ipaddress
import logging
import os
import random
import re
import struct
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Self

from anyio import BrokenResourceError, ClosedResourceError, EndOfStream
from anyio.abc import ByteStream

logger = logging.getLogger(__name__)

BIOCSBLEN = 0xC0044266
BIOCSETIF = 0x8020426C
BIOCIMMEDIATE = 0x80044270
BIOCSHDRCMPLT = 0x80044275
BIOCSSEESENT = 0x80044277
BPF_BUFFER = 1 << 20

ETHERTYPE_IPV6 = 0x86DD
PROTO_TCP = 6
PROTO_ICMPV6 = 58
ICMP_ECHO_REQUEST = 128
ICMP_ECHO_REPLY = 129
ICMP_NEIGHBOR_SOLICIT = 135
ICMP_NEIGHBOR_ADVERT = 136

FIN, SYN, RST, PSH, ACK = 0x01, 0x02, 0x04, 0x08, 0x10
WINDOW = 65535
RETRANSMIT_INITIAL = 0.2
RETRANSMIT_MAX = 3.0
RETRANSMIT_LIMIT = 12


def interface_mac(name: str) -> bytes:
    output = subprocess.run(["ifconfig", name], capture_output=True, text=True, check=True).stdout
    match = re.search(r"ether ([0-9a-f:]{17})", output)
    if match is None:
        raise OSError(f"{name} has no Ethernet address")
    return bytes.fromhex(match.group(1).replace(":", ""))


def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def _pseudo(src: bytes, dst: bytes, proto: int, payload: bytes) -> int:
    return _checksum(src + dst + struct.pack("!I3xB", len(payload), proto) + payload)


def _ipv6(src: bytes, dst: bytes, proto: int, payload: bytes, hop_limit: int = 64) -> bytes:
    return struct.pack("!IHBB", 6 << 28, len(payload), proto, hop_limit) + src + dst + payload


def _seq_lt(a: int, b: int) -> bool:
    return ((a - b) & 0xFFFFFFFF) > 0x7FFFFFFF


class Bpf:
    """Raw frame access to one interface through a BPF device."""

    def __init__(self, interface: str):
        last = None
        for index in range(256):
            try:
                self.fd = os.open(f"/dev/bpf{index}", os.O_RDWR)
                break
            except FileNotFoundError:
                break
            except OSError as error:
                last = error
        else:
            raise OSError("no BPF device is free")
        if not hasattr(self, "fd"):
            raise PermissionError(
                "cannot open a BPF device; run scripts/devicehub-link-macos.sh with sudo after each boot"
            ) from last
        fcntl.ioctl(self.fd, BIOCSBLEN, struct.pack("I", BPF_BUFFER))
        fcntl.ioctl(self.fd, BIOCSETIF, struct.pack("16s16x", interface.encode()))
        fcntl.ioctl(self.fd, BIOCIMMEDIATE, struct.pack("I", 1))
        fcntl.ioctl(self.fd, BIOCSHDRCMPLT, struct.pack("I", 1))
        fcntl.ioctl(self.fd, BIOCSSEESENT, struct.pack("I", 0))

    def frames(self, buffer: bytes):
        offset = 0
        while offset + 18 <= len(buffer):
            caplen, _datalen, hdrlen = struct.unpack_from("=IIH", buffer, offset + 8)
            yield buffer[offset + hdrlen : offset + hdrlen + caplen]
            offset += (hdrlen + caplen + 3) & ~3

    def read(self) -> bytes:
        return os.read(self.fd, BPF_BUFFER)

    def write(self, frame: bytes) -> None:
        os.write(self.fd, frame)

    def close(self) -> None:
        os.close(self.fd)


@dataclass(eq=False)
class TcpConnection(ByteStream):
    stack: "Stack"
    local: tuple[bytes, int]
    remote: tuple[bytes, int]
    peer_mac: bytes
    snd_nxt: int
    rcv_nxt: int
    mss: int
    snd_una: int = 0
    peer_window: int = WINDOW
    established: asyncio.Event = field(default_factory=asyncio.Event)
    _received: deque = field(default_factory=deque)
    _readable: asyncio.Event = field(default_factory=asyncio.Event)
    _writable: asyncio.Event = field(default_factory=asyncio.Event)
    _unsent: bytearray = field(default_factory=bytearray)
    _unacked: bytearray = field(default_factory=bytearray)
    _eof_received: bool = False
    _fin_queued: bool = False
    _fin_sent: bool = False
    _closed: bool = False
    _error: Exception | None = None
    _deadline: float | None = None
    _backoff: float = RETRANSMIT_INITIAL
    _attempts: int = 0

    def __post_init__(self):
        self.snd_una = self.snd_nxt
        self._writable.set()

    @property
    def remote_address(self) -> tuple[str, int]:
        return str(ipaddress.IPv6Address(self.remote[0])), self.remote[1]

    def _segment(self, flags: int, payload: bytes = b"", seq: int | None = None, options: bytes = b"") -> None:
        src, dst = self.local[0], self.remote[0]
        offset = (20 + len(options)) // 4
        header = struct.pack(
            "!HHIIBBHHH",
            self.local[1],
            self.remote[1],
            self.snd_nxt if seq is None else seq,
            self.rcv_nxt,
            offset << 4,
            flags,
            WINDOW,
            0,
            0,
        )
        segment = header + options + payload
        checksum = _pseudo(src, dst, PROTO_TCP, segment)
        segment = segment[:16] + struct.pack("!H", checksum) + segment[18:]
        self.stack.send_ipv6(self.peer_mac, src, dst, PROTO_TCP, segment)

    def _arm(self) -> None:
        if self._deadline is None:
            self._deadline = time.monotonic() + self._backoff

    def flush(self) -> None:
        """Send queued data that fits the peer's window, then a FIN once drained."""
        while self._unsent and len(self._unacked) < self.peer_window:
            size = min(self.mss, len(self._unsent), self.peer_window - len(self._unacked))
            chunk = bytes(self._unsent[:size])
            del self._unsent[:size]
            self._segment(ACK | PSH, chunk)
            self.snd_nxt = (self.snd_nxt + size) & 0xFFFFFFFF
            self._unacked += chunk
            self._arm()
        if self._fin_queued and not self._unsent and not self._fin_sent:
            self._segment(ACK | FIN)
            self.snd_nxt = (self.snd_nxt + 1) & 0xFFFFFFFF
            self._fin_sent = True
            self._arm()
        if len(self._unsent) < 4 * self.mss:
            self._writable.set()

    def on_timer(self, now: float) -> None:
        if self._deadline is None or now < self._deadline:
            return
        self._attempts += 1
        if self._attempts > RETRANSMIT_LIMIT:
            self.fail(BrokenResourceError("TCP peer stopped acknowledging data"))
            return
        if self._unacked:
            self._segment(ACK | PSH, bytes(self._unacked[: self.mss]), seq=self.snd_una)
        elif self._fin_sent:
            self._segment(ACK | FIN, seq=(self.snd_nxt - 1) & 0xFFFFFFFF)
        self._backoff = min(self._backoff * 2, RETRANSMIT_MAX)
        self._deadline = now + self._backoff

    def on_segment(self, seq: int, ack: int, flags: int, window: int, payload: bytes) -> None:  # noqa: C901
        if flags & RST:
            self.fail(BrokenResourceError("TCP connection reset by the Mac"))
            return
        if flags & ACK:
            if ack == self.snd_una and not self.established.is_set():
                self.established.set()  # the ACK that completes the handshake carries no new data
            acked = (ack - self.snd_una) & 0xFFFFFFFF
            in_flight = (self.snd_nxt - self.snd_una) & 0xFFFFFFFF
            if 0 < acked <= in_flight:
                data_acked = min(acked, len(self._unacked))
                del self._unacked[:data_acked]
                self.snd_una = ack
                self._attempts, self._backoff = 0, RETRANSMIT_INITIAL
                self._deadline = None if self.snd_una == self.snd_nxt else time.monotonic() + self._backoff
                if not self.established.is_set():
                    self.established.set()
            self.peer_window = window
        if payload or flags & FIN:
            if seq != self.rcv_nxt:
                self._segment(ACK)  # duplicate ACK asks the Mac to resend from rcv_nxt
                return
            if payload:
                self._received.append(payload)
                self.rcv_nxt = (self.rcv_nxt + len(payload)) & 0xFFFFFFFF
            if flags & FIN:
                self.rcv_nxt = (self.rcv_nxt + 1) & 0xFFFFFFFF
                self._eof_received = True
            self._readable.set()
            self._segment(ACK)
        self.flush()
        if self._fin_sent and self._eof_received and self.snd_una == self.snd_nxt:
            self.stack.forget(self)

    def fail(self, error: Exception) -> None:
        self._error = error
        self._readable.set()
        self._writable.set()
        self.stack.forget(self)

    async def receive(self, max_bytes: int = 65536) -> bytes:
        while not self._received:
            if self._error is not None:
                raise self._error
            if self._eof_received or self._closed:
                raise EndOfStream if not self._closed else ClosedResourceError
            self._readable.clear()
            await self._readable.wait()
        data = self._received.popleft()
        if len(data) > max_bytes:
            self._received.appendleft(data[max_bytes:])
            data = data[:max_bytes]
        return data

    async def send(self, item: bytes) -> None:
        if self._closed or self._fin_queued:
            raise ClosedResourceError
        while len(self._unsent) >= 4 * self.mss:
            if self._error is not None:
                raise self._error
            self._writable.clear()
            await self._writable.wait()
        if self._error is not None:
            raise self._error
        self._unsent += item
        self.flush()

    async def send_eof(self) -> None:
        if not self._fin_queued:
            self._fin_queued = True
            self.flush()

    async def aclose(self) -> None:
        if self._closed:
            return
        await self.send_eof()
        self._closed = True
        self._readable.set()


class Stack:
    """One IPv6 address on a BPF interface, accepting TCP on chosen ports."""

    def __init__(self, interface: str, address: str):
        self.interface = interface
        self.address = ipaddress.IPv6Address(address).packed
        self.mac = interface_mac(interface)
        self.mss = 1500 - 60  # feth links keep the Ethernet MTU
        self._bpf: Bpf | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connections: dict[tuple, TcpConnection] = {}
        self._listeners: dict[int, asyncio.Queue] = {}
        self._reader: threading.Thread | None = None
        self._timer: asyncio.Task | None = None
        self._stopping = False

    async def __aenter__(self) -> Self:
        self._bpf = Bpf(self.interface)
        self._loop = asyncio.get_running_loop()
        self._reader = threading.Thread(target=self._read_frames, name=f"bpf-{self.interface}", daemon=True)
        self._reader.start()
        self._timer = asyncio.create_task(self._timers())
        return self

    async def __aexit__(self, *exc) -> None:
        self._stopping = True
        if self._timer is not None:
            self._timer.cancel()
        for connection in list(self._connections.values()):
            connection._segment(RST | ACK)
            connection.fail(ClosedResourceError())
        if self._bpf is not None:
            self._bpf.close()

    def listen(self, port: int) -> asyncio.Queue:
        """Return a queue of accepted connections for ``port``."""
        return self._listeners.setdefault(port, asyncio.Queue())

    def forget(self, connection: TcpConnection) -> None:
        self._connections.pop((connection.remote, connection.local[1]), None)

    def send_ipv6(self, mac: bytes, src: bytes, dst: bytes, proto: int, payload: bytes, hop_limit: int = 64) -> None:
        assert self._bpf is not None
        frame = mac + self.mac + struct.pack("!H", ETHERTYPE_IPV6) + _ipv6(src, dst, proto, payload, hop_limit)
        try:
            self._bpf.write(frame)
        except OSError as error:
            logger.warning("cannot send on %s: %s", self.interface, error)

    def _read_frames(self) -> None:
        assert self._bpf is not None and self._loop is not None
        while not self._stopping:
            try:
                buffer = self._bpf.read()
            except OSError:
                return
            frames = list(self._bpf.frames(buffer))
            if frames:
                self._loop.call_soon_threadsafe(self._receive_frames, frames)

    async def _timers(self) -> None:
        while True:
            await asyncio.sleep(0.05)
            now = time.monotonic()
            for connection in list(self._connections.values()):
                connection.on_timer(now)

    def _receive_frames(self, frames: list[bytes]) -> None:
        for frame in frames:
            try:
                self._receive_frame(frame)
            except (struct.error, IndexError) as error:
                logger.debug("dropping malformed frame: %s", error)

    def _receive_frame(self, frame: bytes) -> None:
        if len(frame) < 54 or struct.unpack_from("!H", frame, 12)[0] != ETHERTYPE_IPV6:
            return
        peer_mac = frame[6:12]
        ip = frame[14:]
        length, proto = struct.unpack_from("!HB", ip, 4)
        src, dst = ip[8:24], ip[24:40]
        payload = ip[40 : 40 + length]
        if proto == PROTO_ICMPV6:
            self._icmp(peer_mac, src, dst, payload)
        elif proto == PROTO_TCP and dst == self.address:
            self._tcp(peer_mac, src, payload)

    def _icmp(self, peer_mac: bytes, src: bytes, dst: bytes, payload: bytes) -> None:
        kind = payload[0]
        if kind == ICMP_NEIGHBOR_SOLICIT and payload[8:24] == self.address:
            body = struct.pack("!BBHI", ICMP_NEIGHBOR_ADVERT, 0, 0, 0x60000000) + self.address
            body += struct.pack("!BB", 2, 1) + self.mac
            reply_to = src if any(src) else bytes.fromhex("ff020000000000000000000000000001")
            body = body[:2] + struct.pack("!H", _pseudo(self.address, reply_to, PROTO_ICMPV6, body)) + body[4:]
            # Neighbor discovery messages must carry hop limit 255.
            self.send_ipv6(peer_mac, self.address, reply_to, PROTO_ICMPV6, body, hop_limit=255)
        elif kind == ICMP_ECHO_REQUEST and dst == self.address:
            body = bytes((ICMP_ECHO_REPLY, 0, 0, 0)) + payload[4:]
            body = body[:2] + struct.pack("!H", _pseudo(self.address, src, PROTO_ICMPV6, body)) + body[4:]
            self.send_ipv6(peer_mac, self.address, src, PROTO_ICMPV6, body)

    def _tcp(self, peer_mac: bytes, src: bytes, segment: bytes) -> None:
        sport, dport, seq, ack, offset, flags, window = struct.unpack_from("!HHIIBBH", segment)
        payload = segment[(offset >> 4) * 4 :]
        key = ((src, sport), dport)
        connection = self._connections.get(key)
        if connection is not None:
            connection.on_segment(seq, ack, flags, window, payload)
            return
        if flags & RST:
            return
        if flags & SYN and not flags & ACK and dport in self._listeners:
            mss = self.mss
            options = segment[20 : (offset >> 4) * 4]
            index = 0
            while index < len(options) and options[index] != 0:
                if options[index] == 1:
                    index += 1
                    continue
                if options[index] == 2 and index + 4 <= len(options):
                    mss = min(mss, struct.unpack_from("!H", options, index + 2)[0])
                index += max(options[index + 1], 2) if index + 1 < len(options) else len(options)
            connection = TcpConnection(
                stack=self,
                local=(self.address, dport),
                remote=(src, sport),
                peer_mac=peer_mac,
                snd_nxt=random.getrandbits(32),
                rcv_nxt=(seq + 1) & 0xFFFFFFFF,
                mss=mss,
                peer_window=window,
            )
            self._connections[key] = connection
            connection._segment(SYN | ACK, options=struct.pack("!BBH", 2, 4, self.mss))
            connection.snd_nxt = (connection.snd_nxt + 1) & 0xFFFFFFFF
            connection.snd_una = connection.snd_nxt
            self._listeners[dport].put_nowait(connection)
            return
        # Anything else for an unknown connection is answered with a reset.
        reset_seq = ack if flags & ACK else 0
        reset = TcpConnection(
            stack=self,
            local=(self.address, dport),
            remote=(src, sport),
            peer_mac=peer_mac,
            snd_nxt=reset_seq,
            rcv_nxt=(seq + len(payload) + (1 if flags & (SYN | FIN) else 0)) & 0xFFFFFFFF,
            mss=self.mss,
        )
        reset._segment(RST | ACK)


__all__ = ["Stack", "TcpConnection"]
