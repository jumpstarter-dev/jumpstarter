"""Simulated XCP-on-Ethernet TCP server for integration testing.

Implements a minimal subset of the XCP protocol (ASAM MCD-1):
- CONNECT / DISCONNECT
- GET_STATUS
- GET_ID
- SHORT_UPLOAD / DOWNLOAD / SET_MTA
- BUILD_CHECKSUM
- FREE_DAQ / ALLOC_DAQ
- PROGRAM_START / PROGRAM_CLEAR / PROGRAM / PROGRAM_RESET
"""

from __future__ import annotations

import socket
import struct
import threading

import pytest

# XCP command codes (request PIDs)
CMD_CONNECT = 0xFF
CMD_DISCONNECT = 0xFE
CMD_GET_STATUS = 0xFD
CMD_SYNCH = 0xFC
CMD_GET_ID = 0xFA
CMD_SET_MTA = 0xF6
CMD_UPLOAD = 0xF5
CMD_SHORT_UPLOAD = 0xF4
CMD_BUILD_CHECKSUM = 0xF3
CMD_DOWNLOAD = 0xF0
CMD_GET_DAQ_PROCESSOR_INFO = 0xDA
CMD_GET_DAQ_RESOLUTION_INFO = 0xD9
CMD_FREE_DAQ = 0xD6
CMD_ALLOC_DAQ = 0xD5
CMD_PROGRAM_START = 0xD2
CMD_PROGRAM_CLEAR = 0xD1
CMD_PROGRAM = 0xD0
CMD_PROGRAM_RESET = 0xCF

# XCP response codes
RES_OK = 0xFF
RES_ERR = 0xFE

# XCP error codes
ERR_CMD_UNKNOWN = 0x20
ERR_OUT_OF_RANGE = 0x22
ERR_ACCESS_DENIED = 0x24

# Simulated slave properties
MAX_CTO = 8
MAX_DTO = 8
SLAVE_ID = b"XCP_SIM_v1.0"


def _xcp_header(length: int, ctr: int = 0) -> bytes:
    """Build XCP-on-Ethernet transport header: LEN(H) + CTR(H)."""
    return struct.pack("<HH", length, ctr)


def _positive(pid: int, *tail_bytes: int) -> bytes:
    payload = bytes([RES_OK, pid] + list(tail_bytes))
    return _xcp_header(len(payload)) + payload


class MockXcpServer:
    """Minimal XCP-on-Ethernet TCP server for integration testing."""

    def __init__(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(2)
        self._server.settimeout(1.0)
        self.port = self._server.getsockname()[1]
        self._running = True
        self._clients: list[socket.socket] = []
        self._ctr = 0
        self._memory: dict[int, bytes] = {}
        self._mta_address = 0
        self._mta_ext = 0
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self):
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

    def _handle_client(self, conn: socket.socket):
        try:
            while self._running:
                try:
                    hdr = self._recv_exact(conn, 4)
                    if hdr is None:
                        break
                    length, _ctr = struct.unpack("<HH", hdr)
                    payload = self._recv_exact(conn, length)
                    if payload is None:
                        break
                    response = self._dispatch(payload)
                    if response is not None:
                        conn.sendall(response)
                except OSError:
                    break
        finally:
            conn.close()

    @staticmethod
    def _recv_exact(conn: socket.socket, n: int) -> bytes | None:
        data = b""
        while len(data) < n:
            chunk = conn.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    def _make_response(self, payload: bytes) -> bytes:
        self._ctr += 1
        return _xcp_header(len(payload), self._ctr) + payload

    def _make_error(self, err_code: int) -> bytes:
        return self._make_response(bytes([RES_ERR, err_code]))

    def _dispatch(self, payload: bytes) -> bytes | None:
        if not payload:
            return self._make_error(ERR_CMD_UNKNOWN)

        cmd = payload[0]
        handlers = {
            CMD_CONNECT: self._handle_connect,
            CMD_DISCONNECT: self._handle_disconnect,
            CMD_GET_STATUS: self._handle_get_status,
            CMD_GET_ID: self._handle_get_id,
            CMD_SET_MTA: self._handle_set_mta,
            CMD_SHORT_UPLOAD: self._handle_short_upload,
            CMD_DOWNLOAD: self._handle_download,
            CMD_BUILD_CHECKSUM: self._handle_build_checksum,
            CMD_FREE_DAQ: self._handle_free_daq,
            CMD_ALLOC_DAQ: self._handle_alloc_daq,
            CMD_GET_DAQ_PROCESSOR_INFO: self._handle_daq_processor_info,
            CMD_GET_DAQ_RESOLUTION_INFO: self._handle_daq_resolution_info,
            CMD_PROGRAM_START: self._handle_program_start,
            CMD_PROGRAM_CLEAR: self._handle_program_clear,
            CMD_PROGRAM: self._handle_program,
            CMD_PROGRAM_RESET: self._handle_program_reset,
        }

        handler = handlers.get(cmd)
        if handler is None:
            return self._make_error(ERR_CMD_UNKNOWN)
        return handler(payload)

    def _handle_connect(self, _payload: bytes) -> bytes:
        # resource(B) commModeBasic(B) maxCto(B) maxDto(H) protVer(B) transVer(B)
        resource = 0x01 | 0x04  # CAL/PAG + DAQ
        comm_mode = 0x00  # little-endian, no block mode
        resp = struct.pack(
            "<BBBBBHBB",
            RES_OK,
            resource,
            comm_mode,
            0x00,  # reserved
            MAX_CTO,
            MAX_DTO,
            0x01,  # protocol layer version
            0x01,  # transport layer version
        )
        return self._make_response(resp)

    def _handle_disconnect(self, _payload: bytes) -> bytes:
        return self._make_response(bytes([RES_OK]))

    def _handle_get_status(self, _payload: bytes) -> bytes:
        # status(B) protectionStatus(B) configId(H)
        resp = bytes([RES_OK, 0x00, 0x00, 0x00, 0x00, 0x00])
        return self._make_response(resp)

    def _handle_get_id(self, payload: bytes) -> bytes:
        # Returns length then the ID string must be fetched with UPLOAD
        id_bytes = SLAVE_ID
        resp = struct.pack("<BBBBl", RES_OK, 0x00, 0x00, 0x00, len(id_bytes))
        return self._make_response(resp)

    def _handle_set_mta(self, payload: bytes) -> bytes:
        if len(payload) >= 8:
            self._mta_ext = payload[3]
            self._mta_address = struct.unpack_from("<I", payload, 4)[0]
        return self._make_response(bytes([RES_OK]))

    def _handle_short_upload(self, payload: bytes) -> bytes:
        if len(payload) < 8:
            return self._make_error(ERR_OUT_OF_RANGE)
        length = payload[1]
        address = struct.unpack_from("<I", payload, 4)[0]
        data = self._memory.get(address, b"\x00" * length)[:length]
        data = data.ljust(length, b"\x00")
        resp = bytes([RES_OK]) + data
        return self._make_response(resp)

    def _handle_download(self, payload: bytes) -> bytes:
        if len(payload) < 2:
            return self._make_error(ERR_OUT_OF_RANGE)
        length = payload[1]
        data = payload[2 : 2 + length]
        self._memory[self._mta_address] = data
        return self._make_response(bytes([RES_OK]))

    def _handle_build_checksum(self, payload: bytes) -> bytes:
        # Return a fixed checksum for testing
        resp = struct.pack("<BBBxI", RES_OK, 0x01, 0x00, 0x0000DEAD)
        return self._make_response(resp)

    def _handle_free_daq(self, _payload: bytes) -> bytes:
        return self._make_response(bytes([RES_OK]))

    def _handle_alloc_daq(self, _payload: bytes) -> bytes:
        return self._make_response(bytes([RES_OK]))

    def _handle_daq_processor_info(self, _payload: bytes) -> bytes:
        # daqProperties(B) maxDaq(H) maxEventChannel(H) minDaq(B) daqKeyByte(B)
        resp = struct.pack("<BBHBHBB", RES_OK, 0x00, 4, 0, 2, 0, 0x00)
        return self._make_response(resp)

    def _handle_daq_resolution_info(self, _payload: bytes) -> bytes:
        resp = struct.pack(
            "<BBBBBBB",
            RES_OK,
            1,  # granularityOdtEntrySizeDaq
            8,  # maxOdtEntrySizeDaq
            1,  # granularityOdtEntrySizeStim
            8,  # maxOdtEntrySizeStim
            0x22,  # timestampMode
            0x01,  # timestampTicks (low)
        )
        # pad to fill remaining timestamp ticks
        resp += struct.pack("<H", 1)
        return self._make_response(resp)

    def _handle_program_start(self, _payload: bytes) -> bytes:
        resp = struct.pack("<BBBBBBB", RES_OK, 0x00, MAX_CTO, 0, 0, 0, 0)
        return self._make_response(resp)

    def _handle_program_clear(self, _payload: bytes) -> bytes:
        return self._make_response(bytes([RES_OK]))

    def _handle_program(self, _payload: bytes) -> bytes:
        return self._make_response(bytes([RES_OK]))

    def _handle_program_reset(self, _payload: bytes) -> bytes:
        return self._make_response(bytes([RES_OK]))

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
def mock_xcp_server():
    """Start a MockXcpServer on a dynamic port and yield the port number."""
    server = MockXcpServer()
    try:
        yield server.port
    finally:
        server.stop()


# =========================================================================
# Stateful XCP mock master for comprehensive integration-style testing
# =========================================================================


class _SlaveProperties(dict):
    """Mimics pyxcp's SlaveProperties (dict with attribute access)."""

    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name: str, value) -> None:
        self[name] = value


class XcpNotConnected(RuntimeError):
    pass


class XcpSequenceError(RuntimeError):
    pass


class StatefulXcpMaster:
    """A drop-in replacement for pyxcp's Master that enforces XCP state rules.

    Tracks connection state, simulated memory, MTA pointer,
    DAQ list allocation, and programming sequence ordering.
    """

    SLAVE_ID = "XCP_STATEFUL_SIM_v2.0"
    MAX_CTO = 8
    MAX_DTO = 256

    def __init__(self) -> None:
        self._connected = False
        self._memory: dict[int, bytes] = {}
        self._mta_address = 0
        self._mta_ext = 0
        self._daq_lists = 0
        self._daq_ptr: tuple[int, int, int] | None = None
        self._programming = False
        self._program_cleared = False
        self._protection = {
            "dbg": True,
            "pgm": True,
            "stim": False,
            "daq": False,
            "calpag": True,
        }
        self._unlocked = False

        self.slaveProperties = _SlaveProperties(
            maxCto=self.MAX_CTO,
            maxDto=self.MAX_DTO,
            byteOrder="INTEL",
            supportsPgm=True,
            supportsStim=False,
            supportsDaq=True,
            supportsCalpag=True,
            protocolLayerVersion=1,
            transportLayerVersion=1,
            addressGranularity="BYTE",
            slaveBlockMode=False,
        )

    def _require_connected(self):
        if not self._connected:
            raise XcpNotConnected("Not connected – call connect() first")

    # -- session --------------------------------------------------------

    def connect(self, mode: int = 0):
        if self._connected:
            return
        self._connected = True

    def close(self):
        self._connected = False
        self._programming = False
        self._program_cleared = False

    def identifier(self, id_value: int) -> str:
        self._require_connected()
        return self.SLAVE_ID

    def getStatus(self):
        self._require_connected()
        status = _SlaveProperties(store_cal_req=False)
        return status

    def getCurrentProtectionStatus(self) -> dict[str, bool]:
        return dict(self._protection)

    # -- security -------------------------------------------------------

    def cond_unlock(self, resources=None):
        self._require_connected()
        self._unlocked = True
        for key in self._protection:
            self._protection[key] = False

    # -- memory access --------------------------------------------------

    def setMta(self, address: int, ext: int = 0):
        self._require_connected()
        self._mta_address = address
        self._mta_ext = ext

    def shortUpload(self, length: int, address: int, ext: int = 0) -> bytes:
        self._require_connected()
        stored = self._memory.get(address, b"")
        if len(stored) < length:
            stored = stored + b"\x00" * (length - len(stored))
        return stored[:length]

    def download(self, data: bytes):
        self._require_connected()
        self._memory[self._mta_address] = data

    # -- checksum -------------------------------------------------------

    def buildChecksum(self, block_size: int):
        self._require_connected()
        raw = self._memory.get(self._mta_address, b"\x00" * block_size)[:block_size]
        raw = raw.ljust(block_size, b"\x00")
        csum = sum(raw) & 0xFFFFFFFF
        return _SlaveProperties(checksumType=0x01, checksum=csum)

    # -- DAQ ------------------------------------------------------------

    def getDaqInfo(self):
        self._require_connected()
        return {
            "processor": {"minDaq": 0, "maxDaq": max(self._daq_lists, 4)},
            "resolution": {"timestampTicks": 1},
            "channels": [],
        }

    def freeDaq(self):
        self._require_connected()
        self._daq_lists = 0
        self._daq_ptr = None

    def allocDaq(self, daq_count: int):
        self._require_connected()
        self._daq_lists = daq_count

    def allocOdt(self, daq_list_number: int, odt_count: int):
        self._require_connected()
        if daq_list_number >= self._daq_lists:
            raise RuntimeError(f"DAQ list {daq_list_number} not allocated")

    def allocOdtEntry(self, daq_list_number: int, odt_number: int, odt_entries_count: int):
        self._require_connected()

    def setDaqPtr(self, daq_list: int, odt: int, entry: int):
        self._require_connected()
        self._daq_ptr = (daq_list, odt, entry)

    def writeDaq(self, bit_offset: int, size: int, ext: int, address: int):
        self._require_connected()
        if self._daq_ptr is None:
            raise XcpSequenceError("setDaqPtr must be called before writeDaq")

    def setDaqListMode(self, mode: int, daq_list: int, event: int, prescaler: int, priority: int):
        self._require_connected()

    def startStopDaqList(self, mode: int, daq_list: int):
        self._require_connected()

    def startStopSynch(self, mode: int):
        self._require_connected()

    # -- programming ----------------------------------------------------

    def programStart(self):
        self._require_connected()
        self._programming = True
        self._program_cleared = False
        return _SlaveProperties(
            commModePgm=0, maxCtoPgm=self.MAX_CTO,
            maxBsPgm=0, minStPgm=0, queueSizePgm=0,
        )

    def programClear(self, mode: int, clear_range: int):
        self._require_connected()
        if not self._programming:
            raise XcpSequenceError("programStart must be called before programClear")
        self._program_cleared = True
        for addr in list(self._memory):
            if addr < clear_range:
                del self._memory[addr]

    def program(self, data: bytes, block_length: int, last: bool = False):
        self._require_connected()
        if not self._programming:
            raise XcpSequenceError("programStart must be called before program")
        if not self._program_cleared:
            raise XcpSequenceError("programClear must be called before program")
        self._memory[self._mta_address] = data

    def programReset(self, wait_for_optional_response: bool = True):
        self._require_connected()
        self._programming = False
        self._program_cleared = False


@pytest.fixture
def stateful_master():
    """Provide a fresh StatefulXcpMaster instance."""
    return StatefulXcpMaster()
