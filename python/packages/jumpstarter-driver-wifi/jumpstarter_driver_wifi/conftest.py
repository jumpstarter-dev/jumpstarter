"""A stand-in for wmediumd's API socket, strict about the one thing that matters: it does nothing after it
has delivered a frame until that frame is acknowledged, as the real medium does."""

import os
import socket
import struct
import tempfile
import threading

import pytest

from .wmediumd_agent import (
    ATTR_ADDR_RECEIVER,
    ATTR_ADDR_TRANSMITTER,
    ATTR_FRAME,
    ATTR_FREQ,
    CMD_ADD_MAC_ADDR,
    CMD_FRAME,
    MSG_ACK,
    MSG_NETLINK,
    MSG_REGISTER,
    MSG_SET_CONTROL,
    attribute,
    attributes,
)


class FakeMedium:
    def __init__(self, path: str) -> None:
        self.path = path
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen(1)
        self.client: socket.socket | None = None
        self.registered = threading.Event()
        self.hears_all = threading.Event()
        self.acknowledged = threading.Event()
        self.transmitted: list[dict] = []  # frames the bridge sent in
        self.claimed: list[bytes] = []  # addresses the bridge said are its station's
        self.changed = threading.Condition()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _exactly(self, size: int) -> bytes:
        assert self.client is not None
        data = b""
        while len(data) < size:
            chunk = self.client.recv(size - len(data))
            if not chunk:
                raise EOFError
            data += chunk
        return data

    def _serve(self) -> None:
        try:
            self.client, _ = self.server.accept()
            while True:
                kind, size = struct.unpack("<II", self._exactly(8))
                data = self._exactly(size) if size else b""
                if kind == MSG_ACK:
                    self.acknowledged.set()
                    continue
                if kind == MSG_REGISTER:
                    self.registered.set()
                elif kind == MSG_SET_CONTROL:
                    if struct.unpack("<I", data)[0] & 2:
                        self.hears_all.set()
                elif kind == MSG_NETLINK:
                    found = attributes(data)
                    with self.changed:
                        if data[16] == CMD_ADD_MAC_ADDR:
                            self.claimed.append(found[ATTR_ADDR_RECEIVER])
                        elif data[16] == CMD_FRAME:
                            self.transmitted.append(
                                {
                                    "station": found[ATTR_ADDR_TRANSMITTER],
                                    "frame": found[ATTR_FRAME],
                                    "freq": struct.unpack("<I", found[ATTR_FREQ])[0],
                                    "attributes": set(found),
                                }
                            )
                        self.changed.notify_all()
                self.client.sendall(struct.pack("<II", MSG_ACK, 0))  # the medium answers every message
        except (EOFError, OSError):
            pass

    def deliver(self, frame: bytes, freq: int = 2437, timeout: float = 5) -> bool:
        """Deliver a frame to the bridge, and say whether it was acknowledged in time."""
        body = struct.pack("<BBH", CMD_FRAME, 1, 0) + (
            attribute(ATTR_ADDR_RECEIVER, bytes.fromhex("0215b3000000"))
            + attribute(ATTR_FRAME, frame)
            + attribute(ATTR_FREQ, struct.pack("<I", freq))
        )
        message = struct.pack("<IHHII", 16 + len(body), 0x23, 1, 1, 0) + body
        self.acknowledged.clear()
        assert self.client is not None
        self.client.sendall(struct.pack("<II", MSG_NETLINK, len(message)) + message)
        return self.acknowledged.wait(timeout)

    def wait_for(self, what, timeout: float = 5):
        with self.changed:
            return self.changed.wait_for(what, timeout)

    def close(self) -> None:
        for sock in (self.client, self.server):
            if sock is not None:
                sock.close()


@pytest.fixture
def media():
    """As many media as a test asks for: one for each device."""
    made: list[FakeMedium] = []
    with tempfile.TemporaryDirectory(prefix="wm") as directory:  # (short: a unix socket path has a length limit)

        def make() -> FakeMedium:
            made.append(FakeMedium(os.path.join(directory, f"api{len(made)}")))
            return made[-1]

        yield make
        for fake in made:
            fake.close()


@pytest.fixture
def medium(media):
    return media()


@pytest.fixture
def anyio_backend():
    return "asyncio"
