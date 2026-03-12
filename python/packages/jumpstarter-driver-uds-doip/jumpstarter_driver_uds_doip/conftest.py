import socket
import struct
import threading

import pytest

ECU_LOGICAL_ADDRESS = 0x00E0
PROTOCOL_VERSION = 0x02

# DoIP payload types
ROUTING_ACTIVATION_REQUEST = 0x0005
ROUTING_ACTIVATION_RESPONSE = 0x0006
DIAGNOSTIC_MESSAGE = 0x8001
DIAGNOSTIC_MESSAGE_POS_ACK = 0x8002

# UDS service IDs
SID_DIAG_SESSION_CTRL = 0x10
SID_ECU_RESET = 0x11
SID_CLEAR_DTC = 0x14
SID_READ_DTC_INFO = 0x19
SID_READ_DATA_BY_ID = 0x22
SID_SECURITY_ACCESS = 0x27
SID_WRITE_DATA_BY_ID = 0x2E
SID_TESTER_PRESENT = 0x3E


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


def _resp_session_ctrl(request_data: bytes) -> bytes:
    positive_sid = request_data[0] + 0x40
    sub = request_data[1] if len(request_data) > 1 else 0x01
    # P2 server max = 25ms (0x0019), P2* server max = 5000ms (0x01F4)
    return bytes([positive_sid, sub, 0x00, 0x19, 0x01, 0xF4])


def _resp_echo_sub(request_data: bytes) -> bytes:
    positive_sid = request_data[0] + 0x40
    sub = request_data[1] if len(request_data) > 1 else 0x01
    return bytes([positive_sid, sub])


def _resp_tester_present(request_data: bytes) -> bytes:
    return bytes([request_data[0] + 0x40, 0x00])


def _resp_read_did(request_data: bytes) -> bytes:
    positive_sid = request_data[0] + 0x40
    did_hi = request_data[1] if len(request_data) > 1 else 0xF1
    did_lo = request_data[2] if len(request_data) > 2 else 0x90
    return bytes([positive_sid, did_hi, did_lo]) + b"WVWZZZ3CZWE123456"


def _resp_write_did(request_data: bytes) -> bytes:
    positive_sid = request_data[0] + 0x40
    did_hi = request_data[1] if len(request_data) > 1 else 0xF1
    did_lo = request_data[2] if len(request_data) > 2 else 0x90
    return bytes([positive_sid, did_hi, did_lo])


def _resp_security_access(request_data: bytes) -> bytes:
    positive_sid = request_data[0] + 0x40
    sub = request_data[1] if len(request_data) > 1 else 0x01
    if sub % 2 == 1:
        return bytes([positive_sid, sub]) + b"\xDE\xAD\xBE\xEF"
    return bytes([positive_sid, sub])


def _resp_clear_dtc(request_data: bytes) -> bytes:
    return bytes([request_data[0] + 0x40])


def _resp_read_dtc_info(request_data: bytes) -> bytes:
    positive_sid = request_data[0] + 0x40
    sub = request_data[1] if len(request_data) > 1 else 0x02
    dtc_id = struct.pack(">I", 0x00C01234)[1:]
    return bytes([positive_sid, sub, 0xFF]) + dtc_id + bytes([0x2F])


_UDS_HANDLERS = {
    SID_DIAG_SESSION_CTRL: _resp_session_ctrl,
    SID_ECU_RESET: _resp_echo_sub,
    SID_TESTER_PRESENT: _resp_tester_present,
    SID_READ_DATA_BY_ID: _resp_read_did,
    SID_WRITE_DATA_BY_ID: _resp_write_did,
    SID_SECURITY_ACCESS: _resp_security_access,
    SID_CLEAR_DTC: _resp_clear_dtc,
    SID_READ_DTC_INFO: _resp_read_dtc_info,
}


def _build_uds_response(request_data: bytes) -> bytes:
    """Parse a UDS request and return the appropriate positive response bytes."""
    if not request_data:
        return bytes([0x7F, 0x00, 0x11])
    sid = request_data[0]
    handler = _UDS_HANDLERS.get(sid)
    if handler:
        return handler(request_data)
    return bytes([0x7F, sid, 0x11])


class MockDoIPUdsServer:
    """Minimal DoIP TCP server with UDS response logic for integration testing."""

    def __init__(self):
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

    def _accept_loop(self):
        while self._running:
            try:
                conn, _ = self._server.accept()
                conn.settimeout(1.0)
                self._clients.append(conn)
                handler = threading.Thread(target=self._handle_client, args=(conn,), daemon=True)
                handler.start()
            except OSError:
                pass

    def _handle_client(self, conn: socket.socket):
        try:
            while self._running:
                try:
                    result = _read_doip_message(conn)
                    if result is None:
                        break
                    payload_type, payload = result
                    responses = self._dispatch(payload_type, payload)
                    for resp in responses:
                        conn.sendall(resp)
                except OSError:
                    break
        finally:
            conn.close()

    def _dispatch(self, payload_type: int, payload: bytes) -> list[bytes]:
        if payload_type == ROUTING_ACTIVATION_REQUEST:
            client_sa = struct.unpack_from("!H", payload)[0]
            resp = struct.pack("!HHBL", client_sa, ECU_LOGICAL_ADDRESS, 0x10, 0x00000000)
            return [_pack_doip(ROUTING_ACTIVATION_RESPONSE, resp)]

        if payload_type == DIAGNOSTIC_MESSAGE:
            source_addr, target_addr = struct.unpack_from("!HH", payload)
            user_data = payload[4:]

            ack_payload = struct.pack("!HHB", target_addr, source_addr, 0x00)
            ack = _pack_doip(DIAGNOSTIC_MESSAGE_POS_ACK, ack_payload)

            uds_response = _build_uds_response(user_data)
            diag_payload = struct.pack("!HH", target_addr, source_addr) + uds_response
            diag = _pack_doip(DIAGNOSTIC_MESSAGE, diag_payload)

            return [ack, diag]

        return []

    def stop(self):
        self._running = False
        self._server.close()
        for c in self._clients:
            try:
                c.close()
            except OSError:
                pass
        self._thread.join(timeout=3)


@pytest.fixture
def mock_doip_uds_server():
    """Start a MockDoIPUdsServer on a dynamic port and yield the port number."""
    server = MockDoIPUdsServer()
    try:
        yield server.port
    finally:
        server.stop()
