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
SID_AUTHENTICATION = 0x29
SID_WRITE_DATA_BY_ID = 0x2E
SID_ROUTINE_CONTROL = 0x31
SID_REQUEST_FILE_TRANSFER = 0x38
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
NRC_UPLOAD_DOWNLOAD_NOT_ACCEPTED = 0x70

# Authentication return values (ISO-14229-1:2020)
AUTH_RETURN_SUCCESS = 0x00
AUTH_RETURN_CHALLENGE_REQUIRED = 0x01
AUTH_RETURN_OWNERSHIP_VERIFIED = 0x02
AUTH_RETURN_DEAUTHENTICATED = 0x10

# RoutineControl sub-functions
ROUTINE_START = 0x01
ROUTINE_STOP = 0x02
ROUTINE_REQUEST_RESULTS = 0x03

# FileTransfer ModeOfOperation
FT_ADD_FILE = 0x01
FT_DELETE_FILE = 0x02
FT_REPLACE_FILE = 0x03
FT_READ_FILE = 0x04
FT_READ_DIR = 0x05

# Predefined routine IDs for mock
ROUTINE_SELF_TEST = 0xFF00
ROUTINE_CLEAR_LOGS = 0xFF01

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


INITIAL_FILES: dict[str, bytes] = {
    "/logs/crash.bin": b"\x01\x02\x03\x04CRASH_DATA_PAYLOAD",
    "/config/ecu.cfg": b"mode=normal\ndiag=enabled\n",
}


@dataclass
class EcuState:
    """Mutable state for one logical ECU instance."""

    session: int = SESSION_DEFAULT
    security_unlocked: bool = False
    security_seed: bytes = b""
    authenticated: bool = False
    auth_challenge: bytes = b""
    dids: dict[int, bytes] = field(default_factory=lambda: dict(INITIAL_DIDS))
    dtcs: list[tuple[int, int]] = field(default_factory=lambda: list(INITIAL_DTCS))
    routines_running: set[int] = field(default_factory=set)
    files: dict[str, bytes] = field(default_factory=lambda: dict(INITIAL_FILES))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def reset(self) -> None:
        with self._lock:
            self.session = SESSION_DEFAULT
            self.security_unlocked = False
            self.security_seed = b""
            self.authenticated = False
            self.auth_challenge = b""
            self.dtcs = list(INITIAL_DTCS)
            self.routines_running.clear()


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
            SID_AUTHENTICATION: self._uds_authentication,
            SID_CLEAR_DTC: self._uds_clear_dtc,
            SID_READ_DTC_INFO: self._uds_read_dtc_info,
            SID_ROUTINE_CONTROL: self._uds_routine_control,
            SID_REQUEST_FILE_TRANSFER: self._uds_file_transfer,
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
                if not self.state.security_seed:
                    return _nrc(data[0], NRC_CONDITIONS_NOT_CORRECT)
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

    # -- RoutineControl (0x31) ------------------------------------------------

    def _uds_routine_control(self, data: bytes) -> bytes:
        if len(data) < 4:
            return _nrc(data[0], NRC_REQUEST_OUT_OF_RANGE)
        control_type = data[1]
        routine_id = (data[2] << 8) | data[3]

        with self.state._lock:
            if self.state.session == SESSION_DEFAULT:
                return _nrc(data[0], NRC_CONDITIONS_NOT_CORRECT)

            known_routines = {ROUTINE_SELF_TEST, ROUTINE_CLEAR_LOGS}
            if routine_id not in known_routines:
                return _nrc(data[0], NRC_REQUEST_OUT_OF_RANGE)

            if control_type == ROUTINE_START:
                self.state.routines_running.add(routine_id)
                status_record = b"\x01"  # "running"
            elif control_type == ROUTINE_STOP:
                self.state.routines_running.discard(routine_id)
                status_record = b"\x00"  # "stopped"
            elif control_type == ROUTINE_REQUEST_RESULTS:
                if routine_id in self.state.routines_running:
                    status_record = b"\x01"  # still running
                else:
                    status_record = b"\x02\x00"  # completed, result=pass
            else:
                return _nrc(data[0], NRC_REQUEST_OUT_OF_RANGE)

        return bytes([data[0] + 0x40, control_type, data[2], data[3]]) + status_record

    # -- Authentication (0x29) ------------------------------------------------

    def _uds_authentication(self, data: bytes) -> bytes:
        if len(data) < 2:
            return _nrc(data[0], NRC_REQUEST_OUT_OF_RANGE)
        task = data[1]
        positive_sid = data[0] + 0x40

        with self.state._lock:
            # deAuthenticate, task 0
            if task == 0x00:
                self.state.authenticated = False
                self.state.auth_challenge = b""
                return bytes([positive_sid, task, AUTH_RETURN_DEAUTHENTICATED])

            # requestChallengeForAuthentication, task 5
            # Wire request: SID, task, commConfig (1 byte), algorithmIndicator (16 bytes)
            # Wire response: +SID, task, returnValue, algoIndicator (16 bytes),
            #                  lenPrefixed challenge, lenPrefixed neededAdditionalParam
            if task == 0x05:
                algo = data[3:19] if len(data) >= 19 else bytes(16)
                challenge = os.urandom(16)
                self.state.auth_challenge = challenge
                resp = bytes([positive_sid, task, AUTH_RETURN_CHALLENGE_REQUIRED])
                resp += algo
                resp += struct.pack(">H", len(challenge)) + challenge
                resp += struct.pack(">H", 0)  # no neededAdditionalParameter
                return resp

            # verifyProofOfOwnershipUnidirectional, task 6
            # Wire request: SID, task, algorithmIndicator (16 bytes),
            #                lenPrefixed proof, lenPrefixed challenge, lenPrefixed additional
            # Wire response: +SID, task, returnValue, algoIndicator (16 bytes),
            #                  lenPrefixed sessionKeyInfo
            if task == 0x06:
                if not self.state.auth_challenge:
                    return _nrc(data[0], NRC_CONDITIONS_NOT_CORRECT)
                algo = data[2:18] if len(data) >= 18 else bytes(16)
                offset = 18
                if len(data) < offset + 2:
                    return _nrc(data[0], NRC_CONDITIONS_NOT_CORRECT)
                proof_len = struct.unpack_from(">H", data, offset)[0]
                offset += 2
                proof = data[offset:offset + proof_len]
                expected = derive_key(self.state.auth_challenge)
                if proof != expected:
                    return _nrc(data[0], NRC_INVALID_KEY)
                self.state.authenticated = True
                self.state.auth_challenge = b""
                session_key = os.urandom(8)
                resp = bytes([positive_sid, task, AUTH_RETURN_OWNERSHIP_VERIFIED])
                resp += algo
                resp += struct.pack(">H", len(session_key)) + session_key
                return resp

        return _nrc(data[0], NRC_REQUEST_OUT_OF_RANGE)

    # -- RequestFileTransfer (0x38) -------------------------------------------

    def _uds_file_transfer(self, data: bytes) -> bytes:
        if len(data) < 4:
            return _nrc(data[0], NRC_REQUEST_OUT_OF_RANGE)
        moop = data[1]
        path_len = (data[2] << 8) | data[3]
        if len(data) < 4 + path_len:
            return _nrc(data[0], NRC_REQUEST_OUT_OF_RANGE)
        path = data[4:4 + path_len].decode("ascii", errors="replace")

        with self.state._lock:
            if self.state.session == SESSION_DEFAULT:
                return _nrc(data[0], NRC_CONDITIONS_NOT_CORRECT)

            ft_handlers = {
                FT_DELETE_FILE: self._ft_delete,
                FT_READ_FILE: self._ft_read,
                FT_ADD_FILE: self._ft_add_or_replace,
                FT_REPLACE_FILE: self._ft_add_or_replace,
                FT_READ_DIR: self._ft_read_dir,
            }
            handler = ft_handlers.get(moop)
            if handler is None:
                return _nrc(data[0], NRC_UPLOAD_DOWNLOAD_NOT_ACCEPTED)
            return handler(data[0], moop, path)

    def _ft_delete(self, sid: int, moop: int, path: str) -> bytes:
        if path not in self.state.files:
            return _nrc(sid, NRC_REQUEST_OUT_OF_RANGE)
        del self.state.files[path]
        return bytes([sid + 0x40, moop])

    def _ft_read(self, sid: int, moop: int, path: str) -> bytes:
        if path not in self.state.files:
            return _nrc(sid, NRC_REQUEST_OUT_OF_RANGE)
        size = len(self.state.files[path])
        # moop_echo + lfid(1=2bytes) + maxBlockLen(2b) + DFI(0x00) + fsodipl(2b) + uncompressed(2b) + compressed(2b)
        return (bytes([sid + 0x40, moop, 0x02]) + struct.pack(">H", 4096)
                + bytes([0x00]) + struct.pack(">HHH", 2, size, size))

    def _ft_add_or_replace(self, sid: int, moop: int, path: str) -> bytes:
        if moop == FT_REPLACE_FILE and path not in self.state.files:
            return _nrc(sid, NRC_REQUEST_OUT_OF_RANGE)
        self.state.files[path] = b""
        return bytes([sid + 0x40, moop, 0x02]) + struct.pack(">H", 4096) + bytes([0x00])

    def _ft_read_dir(self, sid: int, moop: int, path: str) -> bytes:
        entries = [p for p in self.state.files if p.startswith(path) or path == "/"]
        size = len("\n".join(entries).encode("ascii"))
        return (bytes([sid + 0x40, moop, 0x02]) + struct.pack(">H", 4096)
                + bytes([0x00]) + struct.pack(">HH", 2, size))

    def stop(self) -> None:
        self._running = False
        self._server.close()
        for c in self._clients:
            try:
                c.close()
            except OSError:
                pass
        self._thread.join(timeout=3)
