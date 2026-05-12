"""Minimal SNTP server for the DUT network driver.

Responds to NTPv3/v4 client requests with the current system time so
that DUTs without external NTP access can still synchronise their
clocks.  The server binds to the gateway IP on UDP port 123 and runs
in a background thread.
"""

import logging
import socket
import struct
import threading
import time

logger = logging.getLogger(__name__)

NTP_EPOCH_OFFSET = 2208988800
NTP_PORT = 123
NTP_PACKET_SIZE = 48


def _to_ntp_timestamp(unix_ts: float) -> tuple[int, int]:
    ntp_ts = unix_ts + NTP_EPOCH_OFFSET
    seconds = int(ntp_ts)
    fraction = int((ntp_ts - seconds) * (2**32))
    return seconds, fraction


def _extract_transmit_timestamp(data: bytes) -> tuple[int, int]:
    """Extract the transmit timestamp from an incoming NTP request."""
    if len(data) < NTP_PACKET_SIZE:
        return 0, 0
    tx_sec, tx_frac = struct.unpack("!II", data[40:48])
    return tx_sec, tx_frac


def build_response(recv_ts: float, tx_ts: float, origin: tuple[int, int]) -> bytes:
    """Build a minimal NTPv4 server response packet.

    Parameters
    ----------
    recv_ts:
        Unix timestamp when the request was received.
    tx_ts:
        Unix timestamp for the transmit field of the response.
    origin:
        The transmit timestamp from the client request, copied into the
        origin timestamp field of the response.
    """
    # LI=0 (no warning), VN=4, Mode=4 (server)
    li_vn_mode = (0 << 6) | (4 << 3) | 4
    stratum = 1
    poll = 6
    precision = -20  # ~1 microsecond

    ref_s, ref_f = _to_ntp_timestamp(tx_ts)
    rx_s, rx_f = _to_ntp_timestamp(recv_ts)
    tx_s, tx_f = _to_ntp_timestamp(tx_ts)

    return struct.pack(
        "!BBBb11I",
        li_vn_mode,
        stratum,
        poll,
        precision,
        0,  # root delay
        0,  # root dispersion
        0x4C4F434C,  # reference ID: "LOCL"
        ref_s, ref_f,
        origin[0], origin[1],
        rx_s, rx_f,
        tx_s, tx_f,
    )


class NtpServer:
    """A minimal SNTP server that runs in a background thread."""

    def __init__(self, bind_address: str, port: int = NTP_PORT) -> None:
        self._bind_address = bind_address
        self._port = port
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.settimeout(0.5)
        self._sock.bind((self._bind_address, self._port))
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        logger.info("NTP server started on %s:%d", self._bind_address, self._port)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        logger.info("NTP server stopped")

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop_event.is_set():
            try:
                data, addr = self._sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(data) < NTP_PACKET_SIZE:
                continue
            recv_ts = time.time()
            origin = _extract_transmit_timestamp(data)
            tx_ts = time.time()
            response = build_response(recv_ts, tx_ts, origin)
            try:
                self._sock.sendto(response, addr)
            except OSError:
                break
