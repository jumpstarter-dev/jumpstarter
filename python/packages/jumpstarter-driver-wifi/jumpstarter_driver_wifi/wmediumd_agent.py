"""A frame bridge beside a simulated Wi-Fi medium: wmediumd, or the kernel's ``mac80211_hwsim`` itself.

It runs where the medium's API socket is, joins the medium as one of its stations, and serves the frames of
that station on a TCP port: what the medium's other stations send comes out of the port, and what goes into the
port is sent into the medium. Two such ports joined by a byte stream put two media on one air.

With ``--hwsim`` there is no wmediumd: the bridge is the medium of the ``mac80211_hwsim`` radios of the kernel
(and network namespace) it runs in, over generic netlink. That is for a Linux guest (QEMU) or host that has only
the kernel module: the bridge runs inside it, as root, and its port is what leaves the guest.

The port's wire format is a stream of records, each a 4-byte big-endian length and that many bytes: the framing
QEMU's stream network devices use for Ethernet frames. A Wi-Fi record is a 4-byte big-endian frequency in MHz
followed by one 802.11 frame, without FCS.

Standard library only: it is run with the Python of whatever container the medium is in.

    python3 agent.py --api-socket '/tmp/cf_env_*/env-*/wmediumd_api_server' --listen 127.0.0.1:7310
    python3 agent.py --hwsim --listen 0.0.0.0:7310
"""

from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import select
import socket
import struct
import sys
import time

# wmediumd/api.h
MSG_ACK, MSG_REGISTER, MSG_NETLINK, MSG_SET_CONTROL = 1, 2, 4, 5
CTL_RX_ALL_FRAMES = 1 << 1
# mac80211_hwsim.h
CMD_REGISTER, CMD_FRAME, CMD_TX_INFO_FRAME, CMD_ADD_MAC_ADDR = 1, 2, 3, 7
ATTR_ADDR_RECEIVER, ATTR_ADDR_TRANSMITTER, ATTR_FRAME, ATTR_FLAGS = 1, 2, 3, 4
ATTR_RX_RATE, ATTR_SIGNAL, ATTR_TX_INFO, ATTR_COOKIE, ATTR_FREQ = 5, 6, 7, 8, 19
TX_CTL_NO_ACK, TX_STAT_ACK = 1 << 1, 1 << 2
# netlink.h, genetlink.h
NETLINK_GENERIC, NLM_F_REQUEST, NLMSG_ERROR, GENL_ID_CTRL = 16, 1, 2, 0x10
CTRL_CMD_GETFAMILY, CTRL_ATTR_FAMILY_ID, CTRL_ATTR_FAMILY_NAME = 3, 1, 2
SIGNAL = -30  # dBm: what a station in the same room is heard at

MAX_RECORD = 1 << 16
MAX_BACKLOG = 4 << 20  # what a slow peer may leave unsent before frames are dropped, as a radio drops them
MAX_AGE = 2.0  # seconds a frame may wait for a peer that does not read: after that it is no longer on the air


def mac(text: str) -> bytes:
    return bytes.fromhex(text.replace(":", "").replace("-", ""))


def attribute(kind: int, payload: bytes) -> bytes:
    return struct.pack("<HH", len(payload) + 4, kind) + payload + b"\0" * (-len(payload) % 4)


def attributes(message: bytes) -> dict[int, bytes]:
    """The attributes of a generic netlink message (16 bytes of netlink header, 4 of generic netlink)."""
    found, at = {}, 20
    while at + 4 <= len(message):
        size, kind = struct.unpack_from("<HH", message, at)
        if size < 4:
            break
        found[kind & 0x3FFF] = message[at + 4 : at + size]
        at += (size + 3) & ~3
    return found


def frame_record(freq: int, frame: bytes) -> bytes:
    payload = struct.pack(">I", freq) + frame
    return struct.pack(">I", len(payload)) + payload


def records(buffer: bytearray):
    """Take the whole records out of the front of a buffer: (frequency, frame) each."""
    while len(buffer) >= 4:
        size = struct.unpack_from(">I", buffer)[0]
        if size < 4 or size > MAX_RECORD:
            raise ValueError(f"a record of {size} bytes is not a frame")
        if len(buffer) < 4 + size:
            return
        freq = struct.unpack_from(">I", buffer, 4)[0]
        frame = bytes(buffer[8 : 4 + size])
        del buffer[: 4 + size]
        yield freq, frame


class Medium:
    """The medium's API socket, with this bridge registered as a client that hears every frame."""

    def __init__(self, path: str, station: bytes) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)
        self.station = station
        self.family = 0x1F  # the generic netlink family id; taken from the first message the medium sends
        self.sequence = 0
        self.cookie = 1 << 40
        self.send(MSG_REGISTER)
        self.send(MSG_SET_CONTROL, struct.pack("<I", CTL_RX_ALL_FRAMES))

    def fileno(self) -> int:
        return self.sock.fileno()

    def send(self, kind: int, data: bytes = b"") -> None:
        self.sock.sendall(struct.pack("<II", kind, len(data)) + data)

    def _exactly(self, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise EOFError("the medium closed its API socket")
            data += chunk
        return data

    def receive(self) -> tuple[int, bytes] | None:
        """One message from the medium. A frame is acknowledged at once: the medium does nothing else until
        it is, which is why this bridge has to run beside it."""
        kind, size = struct.unpack("<II", self._exactly(8))
        data = self._exactly(size) if size else b""
        if kind != MSG_NETLINK:
            return None
        self.send(MSG_ACK)
        if len(data) < 20:
            return None
        self.family = struct.unpack_from("<H", data, 4)[0]
        if data[16] != CMD_FRAME:
            return None
        found = attributes(data)
        frame = found.get(ATTR_FRAME)
        if not frame or len(frame) < 16:
            return None
        freq = struct.unpack("<I", found[ATTR_FREQ])[0] if len(found.get(ATTR_FREQ, b"")) == 4 else 2412
        return freq, frame

    def _netlink(self, command: int, payload: bytes) -> None:
        self.sequence += 1
        body = struct.pack("<BBH", command, 1, 0) + payload
        self.send(MSG_NETLINK, struct.pack("<IHHII", 16 + len(body), self.family, 1, self.sequence, 0) + body)

    def claim(self, address: bytes) -> None:
        """Tell the medium that frames to this address are for the bridge's station."""
        self._netlink(
            CMD_ADD_MAC_ADDR, attribute(ATTR_ADDR_TRANSMITTER, self.station) + attribute(ATTR_ADDR_RECEIVER, address)
        )

    def transmit(self, freq: int, frame: bytes) -> None:
        """Send a frame into the medium as the bridge's station. The medium reads every one of these
        attributes without checking that it is there, so none may be left out."""
        self.cookie += 1
        fast = 7 if freq > 4000 else 11  # the fastest legacy rate of the band, then the slowest three times
        rates = struct.pack("<bBbBbBbB", fast, 1, 0, 3, -1, 0, -1, 0)
        self._netlink(
            CMD_FRAME,
            attribute(ATTR_ADDR_TRANSMITTER, self.station)
            + attribute(ATTR_FRAME, frame)
            + attribute(ATTR_FLAGS, struct.pack("<I", 0))
            + attribute(ATTR_TX_INFO, rates)
            + attribute(ATTR_COOKIE, struct.pack("<Q", self.cookie))
            + attribute(ATTR_FREQ, struct.pack("<I", freq)),
        )

    def close(self) -> None:
        self.sock.close()


class Hwsim:
    """The ``mac80211_hwsim`` radios of this kernel and network namespace, with this bridge as their medium.

    Once a process has registered, the kernel hands it every frame a radio sends and delivers nothing itself:
    the bridge gives each frame to the other radios here, tells the sender how it went, and passes it on. When
    the bridge's socket closes the kernel is the medium again.
    """

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_GENERIC)  # ty: ignore[unresolved-attribute]
        self.sock.bind((0, 0))
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
        self.sequence = 0
        self.radios: set[bytes] = set()  # as the kernel names them: learned as each one first sends
        self._send(GENL_ID_CTRL, CTRL_CMD_GETFAMILY, attribute(CTRL_ATTR_FAMILY_NAME, b"MAC80211_HWSIM\0"))
        answer = self.sock.recv(65536)
        kind = struct.unpack_from("<H", answer, 4)[0]
        family = attributes(answer).get(CTRL_ATTR_FAMILY_ID, b"")
        if kind == NLMSG_ERROR or len(family) != 2:
            raise OSError("there is no mac80211_hwsim in this kernel (modprobe mac80211_hwsim)")
        self.family = struct.unpack("<H", family)[0]
        self._send(self.family, CMD_REGISTER)

    def fileno(self) -> int:
        return self.sock.fileno()

    def _send(self, family: int, command: int, payload: bytes = b"") -> None:
        self.sequence += 1
        body = struct.pack("<BBH", command, 1, 0) + payload
        try:
            self.sock.send(struct.pack("<IHHII", 16 + len(body), family, NLM_F_REQUEST, self.sequence, 0) + body)
        except BlockingIOError:
            pass  # (the air is full: a frame is lost)

    def receive(self) -> tuple[int, bytes] | None:
        """One frame a radio here sent: the sender is told at once that it was heard, since nothing far away
        can answer in the time a radio waits."""
        try:
            data = self.sock.recv(65536)
        except OSError as error:
            if error.errno == 105:  # ENOBUFS: frames were lost, as on a crowded air
                return None
            raise
        if len(data) < 20:
            return None
        kind = struct.unpack_from("<H", data, 4)[0]
        if kind == NLMSG_ERROR:
            code = struct.unpack_from("<i", data, 16)[0]
            if code == -1 and not self.radios:  # EPERM, to the registration
                raise OSError("the bridge may not be the medium: it needs CAP_NET_ADMIN (run it as root)")
            return None
        if kind != self.family or data[16] != CMD_FRAME:
            return None
        found = attributes(data)
        frame, radio = found.get(ATTR_FRAME), found.get(ATTR_ADDR_TRANSMITTER)
        if not frame or len(frame) < 16 or not radio:
            return None
        self.radios.add(radio)
        flags = struct.unpack("<I", found[ATTR_FLAGS])[0] if len(found.get(ATTR_FLAGS, b"")) == 4 else 0
        if not flags & TX_CTL_NO_ACK:
            flags |= TX_STAT_ACK
        self._send(
            self.family,
            CMD_TX_INFO_FRAME,
            attribute(ATTR_ADDR_TRANSMITTER, radio)
            + attribute(ATTR_FLAGS, struct.pack("<I", flags))
            + attribute(ATTR_SIGNAL, struct.pack("<i", SIGNAL))
            + attribute(ATTR_TX_INFO, found.get(ATTR_TX_INFO, bytes(8)))
            + attribute(ATTR_COOKIE, found.get(ATTR_COOKIE, bytes(8))),
        )
        freq = struct.unpack("<I", found[ATTR_FREQ])[0] if len(found.get(ATTR_FREQ, b"")) == 4 else 2412
        self._deliver(freq, frame, skip=radio)
        return freq, frame

    def _deliver(self, freq: int, frame: bytes, skip: bytes = b"") -> None:
        """Each radio is given the frame, and takes it if it is on that frequency and the frame is for it."""
        for radio in self.radios:
            if radio != skip:
                self._send(
                    self.family,
                    CMD_FRAME,
                    attribute(ATTR_ADDR_RECEIVER, radio)
                    + attribute(ATTR_FRAME, frame)
                    + attribute(ATTR_RX_RATE, struct.pack("<I", 0))
                    + attribute(ATTR_SIGNAL, struct.pack("<I", SIGNAL & 0xFFFFFFFF))
                    + attribute(ATTR_FREQ, struct.pack("<I", freq)),
                )

    def claim(self, address: bytes) -> None:
        """Nothing to claim: the bridge is given every frame, whomever it is for."""

    def transmit(self, freq: int, frame: bytes) -> None:
        self._deliver(freq, frame)

    def close(self) -> None:
        self.sock.close()


class Bridge:
    def __init__(self, api_socket: str, listen: tuple[str, int], station: bytes, ignore: list[bytes]) -> None:
        self.api_socket = api_socket
        self.station = station
        self.ignore = ignore
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(listen)
        self.listener.listen(1)
        self.medium: Medium | Hwsim | None = None
        self.peer: socket.socket | None = None
        self.incoming = bytearray()
        self.outgoing = bytearray()  # the record being written to the peer
        self.waiting = collections.deque()  # the records after it: (the time it was heard, the record)
        self.backlog = 0
        self.remote: set[bytes] = set()  # the addresses of the stations on the far side
        self.recent: list[bytes] = []  # the last frames heard: the medium gives a copy for each listener
        self.stats = {"heard": 0, "sent": 0, "dropped": 0}
        self.next_look = 0.0
        self.report("listening")

    def report(self, event: str, **more) -> None:
        state = {
            "event": event,
            "port": self.listener.getsockname()[1],
            "attached": self.medium is not None,
            "peer": self.peer is not None,
            "remote_stations": sorted(address.hex(":") for address in self.remote),
            **self.stats,
            **more,
        }
        print(json.dumps(state), flush=True)

    # ---- the medium ------------------------------------------------------------------------------

    def find_medium(self) -> None:
        if time.monotonic() < self.next_look:
            return
        self.next_look = time.monotonic() + 1.0
        if not self.api_socket:
            try:
                self.medium = Hwsim()
            except OSError as error:
                self.report("no-medium", reason=str(error))  # (the module is not loaded yet: looked for again)
                return
            self.remote.clear()
            self.report("medium", path="mac80211_hwsim")
            return
        for path in sorted(glob.glob(self.api_socket)):
            try:
                self.medium = Medium(path, self.station)
            except OSError:
                continue
            self.remote.clear()
            self.report("medium", path=path)
            return

    def lose_medium(self, reason: str) -> None:
        if self.medium is not None:
            self.medium.close()
            self.medium = None
        self.remote.clear()
        self.drop_peer()  # the far side starts again with a medium that knows nothing of it
        self.report("medium-lost", reason=reason)

    def heard(self, freq: int, frame: bytes) -> None:
        """A frame the medium delivers: it goes to the far side, unless that is where it came from."""
        receiver, sender = frame[4:10], frame[10:16]
        if sender in self.remote or any(sender.startswith(prefix) for prefix in self.ignore):
            return
        if not receiver[0] & 1 and receiver not in self.remote:
            return  # to a station on this side
        digest = hashlib.blake2s(frame, digest_size=8).digest()
        if digest in self.recent:
            return
        self.recent.append(digest)
        del self.recent[:-64]
        self.stats["heard"] += 1
        if self.peer is None:
            return
        if self.backlog > MAX_BACKLOG:
            self.stats["dropped"] += 1
            return
        record = frame_record(freq, frame)
        self.waiting.append((time.monotonic(), record))
        self.backlog += len(record)

    # ---- the far side ----------------------------------------------------------------------------

    def accept(self) -> None:
        connection, _ = self.listener.accept()
        self.drop_peer()  # one path at a time; a new one replaces a stale one
        connection.setblocking(False)
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.peer = connection
        self.report("peer")

    def drop_peer(self) -> None:
        if self.peer is not None:
            self.peer.close()
            self.peer = None
            self.report("peer-lost")
        self.incoming.clear()
        self.outgoing.clear()
        self.waiting.clear()
        self.backlog = 0

    def read_peer(self) -> None:
        assert self.peer is not None
        try:
            chunk = self.peer.recv(65536)
        except BlockingIOError:
            return
        except OSError:
            chunk = b""
        if not chunk:
            self.drop_peer()
            return
        self.incoming += chunk
        try:
            for freq, frame in records(self.incoming):
                self.say(freq, frame)
        except ValueError as error:
            self.report("peer-error", reason=str(error))
            self.drop_peer()

    def say(self, freq: int, frame: bytes) -> None:
        """A frame from the far side: this side's medium hears it from the bridge's station."""
        if self.medium is None or len(frame) < 16:
            return
        sender = frame[10:16]
        if sender not in self.remote:
            self.remote.add(sender)
            self.medium.claim(sender)
        self.medium.transmit(freq, frame)
        self.stats["sent"] += 1

    def next_record(self) -> bool:
        """Take the next records to write. One that waited too long is dropped whole: a peer that was not
        reading (a path that was not open yet) does not get old air when it starts to."""
        while len(self.outgoing) < MAX_RECORD and self.waiting:
            heard, record = self.waiting.popleft()
            self.backlog -= len(record)
            if time.monotonic() - heard > MAX_AGE:
                self.stats["dropped"] += 1
                continue
            self.outgoing += record
        return bool(self.outgoing)

    def write_peer(self) -> None:
        assert self.peer is not None
        if not self.next_record():
            return
        try:
            sent = self.peer.send(self.outgoing)
        except BlockingIOError:
            return
        except OSError:
            self.drop_peer()
            return
        del self.outgoing[:sent]

    # ---- the loop --------------------------------------------------------------------------------

    def step(self, timeout: float = 1.0) -> None:
        if self.medium is None:
            self.find_medium()
        readable = [self.listener] + [s for s in (self.medium, self.peer) if s is not None]
        writable = [self.peer] if self.peer is not None and (self.outgoing or self.waiting) else []
        ready, ready_to_write, _ = select.select(readable, writable, [], timeout)
        if self.medium is not None and self.medium in ready:
            try:
                message = self.medium.receive()
            except (EOFError, OSError) as error:
                self.lose_medium(str(error))
            else:
                if message is not None:
                    self.heard(*message)
        if self.listener in ready:
            self.accept()
        if self.peer is not None and self.peer in ready:
            self.read_peer()
        if self.peer is not None and self.peer in ready_to_write:
            self.write_peer()

    def run(self, report_every: float = 10.0) -> None:
        last = time.monotonic()
        while True:
            self.step()
            if time.monotonic() - last >= report_every:
                last = time.monotonic()
                self.report("status")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    medium = parser.add_mutually_exclusive_group(required=True)
    medium.add_argument("--api-socket", default="", help="wmediumd's API socket: a glob, retried until found")
    medium.add_argument("--hwsim", action="store_true", help="no wmediumd: be the medium of this kernel's radios")
    parser.add_argument("--listen", default="127.0.0.1:7310", help="where the frame port listens (port 0: any)")
    parser.add_argument("--station", default="02:15:b3:00:00:00", help="a station of the medium that nothing uses")
    parser.add_argument("--ignore", default="", help="senders not to pass on: comma separated address prefixes")
    args = parser.parse_args(argv)
    host, _, port = args.listen.rpartition(":")
    ignore = [mac(prefix) for prefix in args.ignore.split(",") if prefix]
    try:
        Bridge(args.api_socket, (host or "127.0.0.1", int(port)), mac(args.station), ignore).run()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
