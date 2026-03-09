"""Stateful mock ECU speaking DoIP + UDS for representative diagnostic testing.

Implements a TCP server that speaks DoIP (ISO-13400) framing with a stateful
UDS (ISO-14229) engine underneath. The ECU tracks session state, security
access, a writable DID store, and clearable DTC memory.
"""

from __future__ import annotations

import os
import socket
import struct
import threading
from dataclasses import dataclass, field

ECU_LOGICAL_ADDRESS = 0x00E0
PROTOCOL_VERSION = 0x02

# DoIP payload types
ROUTING_ACTIVATION_REQUEST = 0x0005
ROUTING_ACTIVATION_RESPONSE = 0x0006
DIAGNOSTIC_MESSAGE = 0x8001
DIAGNOSTIC_MESSAGE_POS_ACK = 0x8002

# UDS service IDs (ISO-14229)
SID_DIAG_SESSION_CTRL = 0x10
SID_ECU_RESET = 0x11
SID_CLEAR_DTC = 0x14
SID_READ_DTC_INFO = 0x19
SID_READ_DATA_BY_ID = 0x22
SID_SECURITY_ACCESS = 0x27
SID_WRITE_DATA_BY_ID = 0x2E
SID_TESTER_PRESENT = 0x3E

# UDS session sub-function values
SESSION_DEFAULT = 0x01
SESSION_PROGRAMMING = 0x02
SESSION_EXTENDED = 0x03

# UDS Negative Response Codes
NRC_SERVICE_NOT_SUPPORTED = 0x11
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_OUT_OF_RANGE = 0x31
NRC_INVALID_KEY = 0x35
NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED = 0x37

INITIAL_DTCS: list[tuple[int, int]] = [
    (0xC01234, 0x2F),
    (0xC05678, 0x09),
]

INITIAL_DIDS: dict[int, bytes] = {
    0xF190: b"WVWZZZ3CZWE123456",
    0xF187: b"8V0906264A",
    0xF189: b"SW001.002.003",
    0xF18A: b"ACME-ECU-01",
}


def _pack_doip(payload_type: int, payload: bytes) -> bytes:
    header = struct.pack(
        "!BBHL",
        PROTOCOL_VERSION,
        0xFF ^ PROTOCOL_VERSION,
        payload_type,
        len(payload),
    )
    return header + payload


def _read_doip_message(conn: socket.socket) -> tuple[int, bytes] | None:
    header = b""
    while len(header) < 8:
        chunk = conn.recv(8 - len(header))
        if not chunk:
            return None
        header += chunk
    _ver, _inv, payload_type, payload_len = struct.unpack("!BBHL", header)
    payload = b""
    while len(payload) < payload_len:
        chunk = conn.recv(payload_len - len(payload))
        if not chunk:
            return None
        payload += chunk
    return payload_type, payload


def _nrc(sid: int, code: int) -> bytes:
    return bytes([0x7F, sid, code])


def derive_key(seed: bytes) -> bytes:
    """Derive a security key from a seed (XOR each byte with 0xFF)."""
    return bytes(b ^ 0xFF for b in seed)


@dataclass
class EcuState:
    """Mutable state for one logical ECU instance."""

    session: int = SESSION_DEFAULT
    security_unlocked: bool = False
    security_seed: bytes = b""
    dids: dict[int, bytes] = field(default_factory=lambda: dict(INITIAL_DIDS))
    dtcs: list[tuple[int, int]] = field(default_factory=lambda: list(INITIAL_DTCS))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def reset(self) -> None:
        with self._lock:
            self.session = SESSION_DEFAULT
            self.security_unlocked = False
            self.security_seed = b""
            self.dtcs = list(INITIAL_DTCS)


class MockDiagnosticEcu:
    """DoIP TCP server with a stateful UDS engine for integration testing.

    Supports:
    - Session management (default / extended / programming)
    - Security access with seed/key (XOR 0xFF derivation)
    - Readable/writable DID store (writes require extended + unlocked)
    - Clearable DTC memory (restored on ECU reset)
    - Negative responses for precondition violations
    """

    def __init__(self) -> None:
        self.state = EcuState()
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(2)
        self._server.settimeout(1.0)
        self.port = self._server.getsockname()[1]
        self._running = True
        self._clients: list[socket.socket] = []
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, _ = self._server.accept()
                conn.settimeout(1.0)
                self._clients.append(conn)
                handler = threading.Thread(
                    target=self._handle_client, args=(conn,), daemon=True
                )
                handler.start()
            except OSError:
                pass

    def _handle_client(self, conn: socket.socket) -> None:
        try:
            while self._running:
                try:
                    result = _read_doip_message(conn)
                    if result is None:
                        break
                    payload_type, payload = result
                    responses = self._dispatch_doip(payload_type, payload)
                    for resp in responses:
                        conn.sendall(resp)
                except OSError:
                    break
        finally:
            conn.close()

    def _dispatch_doip(
        self, payload_type: int, payload: bytes
    ) -> list[bytes]:
        if payload_type == ROUTING_ACTIVATION_REQUEST:
            client_sa = struct.unpack_from("!H", payload)[0]
            resp = struct.pack(
                "!HHBL", client_sa, ECU_LOGICAL_ADDRESS, 0x10, 0x00000000
            )
            return [_pack_doip(ROUTING_ACTIVATION_RESPONSE, resp)]

        if payload_type == DIAGNOSTIC_MESSAGE:
            source_addr, target_addr = struct.unpack_from("!HH", payload)
            user_data = payload[4:]

            ack_payload = struct.pack("!HHB", target_addr, source_addr, 0x00)
            ack = _pack_doip(DIAGNOSTIC_MESSAGE_POS_ACK, ack_payload)

            uds_response = self._handle_uds(user_data)
            diag_payload = (
                struct.pack("!HH", target_addr, source_addr) + uds_response
            )
            diag = _pack_doip(DIAGNOSTIC_MESSAGE, diag_payload)

            return [ack, diag]

        return []

    # -- UDS stateful engine --------------------------------------------------

    def _handle_uds(self, data: bytes) -> bytes:
        if not data:
            return _nrc(0x00, NRC_SERVICE_NOT_SUPPORTED)

        sid = data[0]
        handlers = {
            SID_DIAG_SESSION_CTRL: self._uds_session_ctrl,
            SID_ECU_RESET: self._uds_ecu_reset,
            SID_TESTER_PRESENT: self._uds_tester_present,
            SID_READ_DATA_BY_ID: self._uds_read_did,
            SID_WRITE_DATA_BY_ID: self._uds_write_did,
            SID_SECURITY_ACCESS: self._uds_security_access,
            SID_CLEAR_DTC: self._uds_clear_dtc,
            SID_READ_DTC_INFO: self._uds_read_dtc_info,
        }
        handler = handlers.get(sid)
        if handler is None:
            return _nrc(sid, NRC_SERVICE_NOT_SUPPORTED)
        return handler(data)

    def _uds_session_ctrl(self, data: bytes) -> bytes:
        sub = data[1] if len(data) > 1 else SESSION_DEFAULT
        with self.state._lock:
            self.state.session = sub
            self.state.security_unlocked = False
            self.state.security_seed = b""
        positive_sid = data[0] + 0x40
        return bytes([positive_sid, sub, 0x00, 0x19, 0x01, 0xF4])

    def _uds_ecu_reset(self, data: bytes) -> bytes:
        sub = data[1] if len(data) > 1 else 0x01
        self.state.reset()
        return bytes([data[0] + 0x40, sub])

    def _uds_tester_present(self, data: bytes) -> bytes:
        return bytes([data[0] + 0x40, 0x00])

    def _uds_read_did(self, data: bytes) -> bytes:
        if len(data) < 3:
            return _nrc(data[0], NRC_REQUEST_OUT_OF_RANGE)
        did = (data[1] << 8) | data[2]
        with self.state._lock:
            value = self.state.dids.get(did)
        if value is None:
            return _nrc(data[0], NRC_REQUEST_OUT_OF_RANGE)
        return bytes([data[0] + 0x40, data[1], data[2]]) + value

    def _uds_write_did(self, data: bytes) -> bytes:
        if len(data) < 4:
            return _nrc(data[0], NRC_REQUEST_OUT_OF_RANGE)
        did = (data[1] << 8) | data[2]
        with self.state._lock:
            if did not in self.state.dids:
                return _nrc(data[0], NRC_REQUEST_OUT_OF_RANGE)
            if self.state.session != SESSION_EXTENDED:
                return _nrc(data[0], NRC_CONDITIONS_NOT_CORRECT)
            if not self.state.security_unlocked:
                return _nrc(data[0], NRC_CONDITIONS_NOT_CORRECT)
            self.state.dids[did] = data[3:]
        return bytes([data[0] + 0x40, data[1], data[2]])

    def _uds_security_access(self, data: bytes) -> bytes:
        sub = data[1] if len(data) > 1 else 0x01
        with self.state._lock:
            if self.state.session == SESSION_DEFAULT:
                return _nrc(data[0], NRC_CONDITIONS_NOT_CORRECT)

            if sub % 2 == 1:
                seed = os.urandom(4)
                self.state.security_seed = seed
                return bytes([data[0] + 0x40, sub]) + seed
            else:
                expected_key = derive_key(self.state.security_seed)
                provided_key = data[2:]
                if provided_key != expected_key:
                    return _nrc(data[0], NRC_INVALID_KEY)
                self.state.security_unlocked = True
                self.state.security_seed = b""
                return bytes([data[0] + 0x40, sub])

    def _uds_clear_dtc(self, data: bytes) -> bytes:
        with self.state._lock:
            self.state.dtcs.clear()
        return bytes([data[0] + 0x40])

    def _uds_read_dtc_info(self, data: bytes) -> bytes:
        sub = data[1] if len(data) > 1 else 0x02
        mask = data[2] if len(data) > 2 else 0xFF
        positive_sid = data[0] + 0x40
        result = bytes([positive_sid, sub, mask])
        with self.state._lock:
            for dtc_id, status in self.state.dtcs:
                if status & mask:
                    result += struct.pack(">I", dtc_id)[1:] + bytes([status])
        return result

    def stop(self) -> None:
        self._running = False
        self._server.close()
        for c in self._clients:
            try:
                c.close()
            except OSError:
                pass
        self._thread.join(timeout=3)
