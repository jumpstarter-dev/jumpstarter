import socket
import struct
import threading

import pytest

PROTOCOL_VERSION = 0x01
INTERFACE_VERSION = 0x01

# SOME/IP message types
MSG_TYPE_REQUEST = 0x00
MSG_TYPE_RESPONSE = 0x80
MSG_TYPE_NOTIFICATION = 0x02
MSG_TYPE_ERROR = 0x81

# Return codes
RC_OK = 0x00
RC_NOT_OK = 0x01
RC_UNKNOWN_SERVICE = 0x02
RC_UNKNOWN_METHOD = 0x03

HEADER_SIZE = 16


def _pack_someip(
    service_id: int,
    method_id: int,
    client_id: int,
    session_id: int,
    message_type: int,
    return_code: int,
    payload: bytes,
) -> bytes:
    """Pack a SOME/IP message into wire format (big-endian)."""
    length = 8 + len(payload)
    header = struct.pack(
        "!HHIHHBBBB",
        service_id,
        method_id,
        length,
        client_id,
        session_id,
        PROTOCOL_VERSION,
        INTERFACE_VERSION,
        message_type,
        return_code,
    )
    return header + payload


def _read_someip_message(conn: socket.socket) -> tuple[int, int, int, int, int, int, bytes] | None:
    """Read a single SOME/IP message from a TCP socket.

    Returns (service_id, method_id, client_id, session_id,
             message_type, return_code, payload) or None on disconnect.
    """
    header = b""
    while len(header) < HEADER_SIZE:
        chunk = conn.recv(HEADER_SIZE - len(header))
        if not chunk:
            return None
        header += chunk

    service_id, method_id, length, client_id, session_id, proto_ver, iface_ver, msg_type, ret_code = struct.unpack(
        "!HHIHHBBBB", header
    )

    payload_len = length - 8
    payload = b""
    while len(payload) < payload_len:
        chunk = conn.recv(payload_len - len(payload))
        if not chunk:
            return None
        payload += chunk

    return service_id, method_id, client_id, session_id, msg_type, ret_code, payload


class MockSomeIpServer:
    """Minimal SOME/IP TCP server for integration testing.

    Handles RPC requests by echoing the payload back in a response.
    """

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
                    result = _read_someip_message(conn)
                    if result is None:
                        break
                    service_id, method_id, client_id, session_id, msg_type, ret_code, payload = result
                    responses = self._dispatch(
                        service_id, method_id, client_id, session_id, msg_type, payload
                    )
                    for resp in responses:
                        conn.sendall(resp)
                except OSError:
                    break
        finally:
            conn.close()

    def _dispatch(
        self,
        service_id: int,
        method_id: int,
        client_id: int,
        session_id: int,
        msg_type: int,
        payload: bytes,
    ) -> list[bytes]:
        if msg_type == MSG_TYPE_REQUEST:
            return [
                _pack_someip(
                    service_id,
                    method_id,
                    client_id,
                    session_id,
                    MSG_TYPE_RESPONSE,
                    RC_OK,
                    payload,
                )
            ]
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
def mock_someip_server():
    """Start a MockSomeIpServer on a dynamic port and yield the port number."""
    server = MockSomeIpServer()
    try:
        yield server.port
    finally:
        server.stop()
