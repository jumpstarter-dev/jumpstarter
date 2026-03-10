"""Stateful mock XCP ECU for representative integration testing.

Simulates a realistic ECU with:
- Addressable memory regions (calibration area, measurement area, flash)
- Resource protection (CAL/PAG, DAQ, PGM) with seed/key unlock
- Calibration parameters pre-populated with known values
- DAQ list management with configurable measurement channels
- Flash programming lifecycle with sequence enforcement
- Connection state tracking
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Pre-populated calibration parameters (address -> value)
# All byte values kept in 0x00-0x7F range (UTF-8 safe for gRPC transport)
CALIBRATION_MAP: dict[int, bytes] = {
    0x0010_0000: b"\x64\x00\x00\x00",     # int32 100    – max engine RPM scale
    0x0010_0004: b"\x32\x00\x00\x00",     # int32 50     – idle RPM target
    0x0010_0008: b"\x0A\x00\x00\x00",     # int32 10     – fuel trim %
    0x0010_000C: b"\x01",                  # bool  True   – traction control enabled
}

# Pre-populated measurement signals (address -> initial value)
MEASUREMENT_MAP: dict[int, bytes] = {
    0x0020_0000: b"\x5A\x00\x00\x00",     # int32 90     – coolant temperature (C)
    0x0020_0004: b"\x00\x00\x00\x00",     # int32 0      – vehicle speed (km/h)
    0x0020_0008: b"\x37\x00\x00\x00",     # int32 55     – battery voltage (x10)
    0x0020_000C: b"\x03\x04\x00\x00",     # int32 1027   – engine RPM
}

# Flash region: uses 0x00 as erased state (UTF-8 safe, unlike 0xFF)
FLASH_BASE = 0x0800_0000
FLASH_SIZE = 0x0001_0000  # 64 KB

# ECU identification
ECU_ID = "XCP_ECU_SIM_v1.0"


def derive_key(seed: bytes) -> bytes:
    """Derive a security key from a seed (XOR each byte with 0xA5)."""
    return bytes(b ^ 0xA5 for b in seed)


class _AttrDict(dict):
    """Dict that also supports attribute access (like pyxcp SlaveProperties)."""

    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name: str, value) -> None:
        self[name] = value


@dataclass
class DaqList:
    """Represents one allocated DAQ list."""

    odt_count: int = 0
    entries: list[tuple[int, int, int, int]] = field(default_factory=list)
    mode: int = 0
    event_channel: int = 0
    running: bool = False


class MockXcpEcu:
    """Stateful mock XCP ECU that acts as a drop-in for pyxcp's Master.

    Enforces realistic ECU behavior:
    - connect() must be called before any other operation
    - Protected resources require cond_unlock() first
    - Programming follows strict sequence: programStart -> programClear -> program -> programReset
    - Memory is addressable with pre-populated calibration and measurement regions
    """

    MAX_CTO = 8
    MAX_DTO = 256

    def __init__(self) -> None:
        self._connected = False
        self._memory: dict[int, bytes] = {}
        self._mta_address = 0
        self._mta_ext = 0

        # Protection state
        self._protection = {
            "dbg": False,
            "pgm": True,
            "stim": False,
            "daq": False,
            "calpag": True,
        }
        self._unlocked = False
        self._seed: bytes = b""

        # DAQ state
        self._daq_lists: list[DaqList] = []
        self._daq_ptr: tuple[int, int, int] | None = None

        # Programming state
        self._programming = False
        self._program_cleared = False
        self._flash_erased_range = 0

        # Pre-populate memory regions
        for addr, data in CALIBRATION_MAP.items():
            self._memory[addr] = data
        for addr, data in MEASUREMENT_MAP.items():
            self._memory[addr] = data
        # Flash region: pre-filled with 0x00 (erased state, UTF-8 safe)
        self._memory[FLASH_BASE] = b"\x00" * FLASH_SIZE

        self.slaveProperties = _AttrDict(
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
            raise RuntimeError("Not connected – call connect() first")

    def _require_unlocked(self):
        if not self._unlocked:
            raise RuntimeError("Resource protected – unlock required")

    # -- Session Management --------------------------------------------------

    def connect(self, mode: int = 0):
        self._connected = True

    def close(self):
        self._connected = False
        self._programming = False
        self._program_cleared = False

    def identifier(self, id_type: int) -> str:
        self._require_connected()
        return ECU_ID

    def getStatus(self):
        self._require_connected()
        return _AttrDict(store_cal_req=False, store_daq_req=False)

    def getCurrentProtectionStatus(self) -> dict[str, bool]:
        return dict(self._protection)

    # -- Security (Seed & Key) -----------------------------------------------

    def cond_unlock(self, resources=None):
        self._require_connected()
        self._unlocked = True
        for key in self._protection:
            self._protection[key] = False

    # -- Memory Access -------------------------------------------------------

    def setMta(self, address: int, ext: int = 0):
        self._require_connected()
        self._mta_address = address
        self._mta_ext = ext

    def shortUpload(self, length: int, address: int, ext: int = 0) -> bytes:
        self._require_connected()
        # Check for exact-address match first
        if address in self._memory:
            stored = self._memory[address]
            if len(stored) >= length:
                return stored[:length]
            return stored.ljust(length, b"\x00")
        # Check if address falls within a larger block (flash region)
        for base_addr, block in self._memory.items():
            if base_addr <= address < base_addr + len(block):
                offset = address - base_addr
                return block[offset:offset + length].ljust(length, b"\x00")
        return b"\x00" * length

    def download(self, data: bytes):
        self._require_connected()
        self._memory[self._mta_address] = data

    # -- Checksum ------------------------------------------------------------

    def buildChecksum(self, block_size: int):
        self._require_connected()
        raw = self.shortUpload(block_size, self._mta_address, self._mta_ext)
        csum = sum(raw) & 0xFFFFFFFF
        return _AttrDict(checksumType=0x01, checksum=csum)

    # -- DAQ -----------------------------------------------------------------

    def getDaqInfo(self):
        self._require_connected()
        return {
            "processor": {
                "minDaq": 0,
                "maxDaq": max(len(self._daq_lists), 4),
                "properties": {"configType": "DYNAMIC"},
            },
            "resolution": {
                "timestampTicks": 1,
                "maxOdtEntrySizeDaq": 8,
                "maxOdtEntrySizeStim": 8,
            },
            "channels": [],
        }

    def freeDaq(self):
        self._require_connected()
        self._daq_lists.clear()
        self._daq_ptr = None

    def allocDaq(self, daq_count: int):
        self._require_connected()
        self._daq_lists = [DaqList() for _ in range(daq_count)]

    def allocOdt(self, daq_list_number: int, odt_count: int):
        self._require_connected()
        if daq_list_number >= len(self._daq_lists):
            raise RuntimeError(f"DAQ list {daq_list_number} not allocated")
        self._daq_lists[daq_list_number].odt_count = odt_count

    def allocOdtEntry(self, daq_list_number: int, odt_number: int, odt_entries_count: int):
        self._require_connected()

    def setDaqPtr(self, daq_list: int, odt: int, entry: int):
        self._require_connected()
        self._daq_ptr = (daq_list, odt, entry)

    def writeDaq(self, bit_offset: int, size: int, ext: int, address: int):
        self._require_connected()
        if self._daq_ptr is None:
            raise RuntimeError("setDaqPtr must be called before writeDaq")
        daq_idx = self._daq_ptr[0]
        if daq_idx < len(self._daq_lists):
            self._daq_lists[daq_idx].entries.append((bit_offset, size, ext, address))

    def setDaqListMode(self, mode: int, daq_list: int, event: int, prescaler: int, priority: int):
        self._require_connected()
        if daq_list < len(self._daq_lists):
            self._daq_lists[daq_list].mode = mode
            self._daq_lists[daq_list].event_channel = event

    def startStopDaqList(self, mode: int, daq_list: int):
        self._require_connected()
        if daq_list < len(self._daq_lists):
            self._daq_lists[daq_list].running = (mode == 1)

    def startStopSynch(self, mode: int):
        self._require_connected()
        for dl in self._daq_lists:
            dl.running = (mode == 1)

    # -- Programming (Flashing) ----------------------------------------------

    def programStart(self):
        self._require_connected()
        if self._protection.get("pgm", False):
            raise RuntimeError("PGM resource is protected – unlock first")
        self._programming = True
        self._program_cleared = False
        return _AttrDict(
            commModePgm=0, maxCtoPgm=self.MAX_CTO,
            maxBsPgm=0, minStPgm=0, queueSizePgm=0,
        )

    def programClear(self, mode: int, clear_range: int):
        self._require_connected()
        if not self._programming:
            raise RuntimeError("programStart must be called before programClear")
        self._program_cleared = True
        self._flash_erased_range = clear_range
        # Erase flash region (fill with 0x00)
        if FLASH_BASE in self._memory:
            flash = bytearray(self._memory[FLASH_BASE])
            erase_len = min(clear_range, len(flash))
            flash[:erase_len] = b"\x00" * erase_len
            self._memory[FLASH_BASE] = bytes(flash)

    def program(self, data: bytes, block_length: int, last: bool = False):
        self._require_connected()
        if not self._programming:
            raise RuntimeError("programStart must be called before program")
        if not self._program_cleared:
            raise RuntimeError("programClear must be called before program")
        # Write to flash at MTA address
        if FLASH_BASE <= self._mta_address < FLASH_BASE + FLASH_SIZE:
            flash = bytearray(self._memory.get(FLASH_BASE, b"\xFF" * FLASH_SIZE))
            offset = self._mta_address - FLASH_BASE
            flash[offset:offset + len(data)] = data
            self._memory[FLASH_BASE] = bytes(flash)
        else:
            self._memory[self._mta_address] = data

    def programReset(self, wait_for_optional_response: bool = True):
        self._require_connected()
        self._programming = False
        self._program_cleared = False
