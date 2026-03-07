import struct
import threading

import can
import isotp
import pytest
from jumpstarter_driver_can.common import IsoTpParams

RXID = 0x641
TXID = 0x642

# UDS service IDs
SID_DIAG_SESSION_CTRL = 0x10
SID_ECU_RESET = 0x11
SID_CLEAR_DTC = 0x14
SID_READ_DTC_INFO = 0x19
SID_READ_DATA_BY_ID = 0x22
SID_SECURITY_ACCESS = 0x27
SID_WRITE_DATA_BY_ID = 0x2E
SID_TESTER_PRESENT = 0x3E


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


class MockUdsEcu:
    """Simulated ECU that responds to UDS requests over a virtual CAN/ISO-TP bus."""

    def __init__(self, channel: str, rxid: int, txid: int):
        self._bus = can.Bus(channel=channel, interface="virtual")
        self._notifier = can.Notifier(self._bus, [])
        address = isotp.Address(rxid=rxid, txid=txid)
        params = IsoTpParams(blocking_send=True)
        self._stack = isotp.NotifierBasedCanStack(
            self._bus,
            self._notifier,
            address=address,
            params=params.model_dump(),
        )
        self._stack.start()
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while self._running:
            data = self._stack.recv(block=True, timeout=0.1)
            if data is not None:
                response = _build_uds_response(data)
                self._stack.send(response, send_timeout=2)

    def stop(self):
        self._running = False
        self._thread.join(timeout=2)
        self._stack.stop()
        self._notifier.stop()
        self._bus.shutdown()


@pytest.fixture
def mock_uds_ecu(request):
    """Start a MockUdsEcu on a virtual CAN bus and yield (channel, rxid, txid) for the driver."""
    channel = request.node.name
    ecu = MockUdsEcu(channel=channel, rxid=TXID, txid=RXID)
    try:
        yield channel, RXID, TXID
    finally:
        ecu.stop()
