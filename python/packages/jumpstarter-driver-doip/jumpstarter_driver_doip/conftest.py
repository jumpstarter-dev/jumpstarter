import socket
import struct
import threading

import pytest

ECU_LOGICAL_ADDRESS = 0x00E0
CLIENT_LOGICAL_ADDRESS = 0x0E00
PROTOCOL_VERSION = 0x02

# DoIP payload types
ROUTING_ACTIVATION_REQUEST = 0x0005
ROUTING_ACTIVATION_RESPONSE = 0x0006
ALIVE_CHECK_REQUEST = 0x0007
ALIVE_CHECK_RESPONSE = 0x0008
VEHICLE_ID_REQUEST = 0x0001
VEHICLE_ID_REQUEST_WITH_VIN = 0x0003
VEHICLE_ID_RESPONSE = 0x0004
ENTITY_STATUS_REQUEST = 0x4001
ENTITY_STATUS_RESPONSE = 0x4002
DIAG_POWER_MODE_REQUEST = 0x4003
DIAG_POWER_MODE_RESPONSE = 0x4004
DIAGNOSTIC_MESSAGE = 0x8001
DIAGNOSTIC_MESSAGE_POS_ACK = 0x8002


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
    """Read a single DoIP message from the socket. Returns (payload_type, payload) or None."""
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


def _handle_routing_activation(payload: bytes) -> bytes:
    client_sa = struct.unpack_from("!H", payload)[0]
    # Response: client_logical_address(H), ecu_logical_address(H), response_code(B=0x10=Success), reserved(L=0)
    resp = struct.pack("!HHBL", client_sa, ECU_LOGICAL_ADDRESS, 0x10, 0x00000000)
    return _pack_doip(ROUTING_ACTIVATION_RESPONSE, resp)


def _handle_vehicle_identification() -> bytes:
    vin = b"WVWZZZ3CZWE123456"
    eid = b"\x00\x01\x02\x03\x04\x05"
    gid = b"\x00\x01\x02\x03\x04\x05"
    resp = struct.pack("!17sH6s6sBB", vin, ECU_LOGICAL_ADDRESS, eid, gid, 0x00, 0x00)
    return _pack_doip(VEHICLE_ID_RESPONSE, resp)


def _handle_entity_status() -> bytes:
    # node_type(B=0), max_sockets(B=16), open_sockets(B=1), max_data_size(L=4096)
    resp = struct.pack("!BBBL", 0x00, 16, 1, 4096)
    return _pack_doip(ENTITY_STATUS_RESPONSE, resp)


def _handle_alive_check() -> bytes:
    resp = struct.pack("!H", ECU_LOGICAL_ADDRESS)
    return _pack_doip(0x0008, resp)


def _handle_diag_power_mode() -> bytes:
    resp = struct.pack("!B", 0x01)  # Ready
    return _pack_doip(DIAG_POWER_MODE_RESPONSE, resp)


def _handle_diagnostic_message(payload: bytes) -> list[bytes]:
    """Handle a diagnostic message: return positive ack + echo response."""
    source_addr, target_addr = struct.unpack_from("!HH", payload)
    user_data = payload[4:]

    # Positive ack
    ack_payload = struct.pack("!HHB", target_addr, source_addr, 0x00)
    ack = _pack_doip(DIAGNOSTIC_MESSAGE_POS_ACK, ack_payload)

    # Echo the diagnostic data back (reversed source/target)
    echo_payload = struct.pack("!HH", target_addr, source_addr) + user_data
    echo = _pack_doip(DIAGNOSTIC_MESSAGE, echo_payload)

    return [ack, echo]


class MockDoIPServer:
    """Minimal DoIP TCP server for integration testing."""

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
            return [_handle_routing_activation(payload)]
        if payload_type in (VEHICLE_ID_REQUEST, VEHICLE_ID_REQUEST_WITH_VIN):
            return [_handle_vehicle_identification()]
        if payload_type == ENTITY_STATUS_REQUEST:
            return [_handle_entity_status()]
        if payload_type == ALIVE_CHECK_REQUEST:
            return [_handle_alive_check()]
        if payload_type == DIAG_POWER_MODE_REQUEST:
            return [_handle_diag_power_mode()]
        if payload_type == DIAGNOSTIC_MESSAGE:
            return _handle_diagnostic_message(payload)
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
def mock_doip_server():
    """Start a MockDoIPServer on a dynamic port and yield the port number."""
    server = MockDoIPServer()
    try:
        yield server.port
    finally:
        server.stop()
