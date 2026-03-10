"""Representative end-to-end XCP ECU tests using jumpstarter.

Demonstrates realistic XCP workflows: connection, measurement, calibration,
DAQ configuration, and flash programming -- all running through the full
jumpstarter driver/gRPC/client pipeline against a stateful mock ECU.
"""

import struct

import pytest

from .mock_ecu import (
    CALIBRATION_MAP,
    ECU_ID,
    FLASH_BASE,
    FLASH_SIZE,
    MEASUREMENT_MAP,
)
from jumpstarter.client.core import DriverError


def _to_bytes(data) -> bytes:
    """Normalize data returned through the gRPC boundary to bytes."""
    return bytes(data, "latin-1") if isinstance(data, str) else data


# -- Full workflow tests -------------------------------------------------------


def test_full_measurement_and_calibration_workflow(ecu_client, mock_ecu):
    """Complete ECU workflow: connect, identify, read measurements, unlock,
    calibrate, verify, checksum, disconnect."""

    # 1. Connect and verify negotiated properties
    info = ecu_client.connect()
    assert info.max_cto == 8
    assert info.max_dto == 256
    assert info.supports_calpag is True
    assert info.supports_daq is True
    assert info.supports_pgm is True

    # 2. Identify the ECU
    ident = ecu_client.get_id(1)
    assert ident.identifier == ECU_ID

    # 3. Read initial calibration parameter (max RPM scale = 100)
    rpm_scale_addr = 0x0010_0000
    data = _to_bytes(ecu_client.upload(4, rpm_scale_addr, 0))
    rpm_scale = struct.unpack("<I", data)[0]
    assert rpm_scale == 100

    # 4. Read initial measurement (coolant temp = 90)
    coolant_addr = 0x0020_0000
    data = _to_bytes(ecu_client.upload(4, coolant_addr, 0))
    coolant_temp = struct.unpack("<I", data)[0]
    assert coolant_temp == 90

    # 5. Check protection status -- calpag should be protected
    status = ecu_client.get_status()
    assert status.resource_protection["calpag"] is True
    assert status.resource_protection["pgm"] is True

    # 6. Unlock resources
    prot = ecu_client.unlock()
    assert prot["calpag"] is False
    assert prot["pgm"] is False

    # 7. Calibrate: change max RPM scale from 100 to 120
    new_rpm_scale = struct.pack("<I", 120)
    ecu_client.download(rpm_scale_addr, new_rpm_scale, 0)

    # 8. Read back and verify the calibration change
    data = _to_bytes(ecu_client.upload(4, rpm_scale_addr, 0))
    assert struct.unpack("<I", data)[0] == 120

    # 9. Checksum verification over the calibration block
    ecu_client.set_mta(rpm_scale_addr, 0)
    csum = ecu_client.build_checksum(4)
    assert csum.checksum_type == 1
    assert csum.checksum_value == sum(new_rpm_scale)

    # 10. Disconnect
    ecu_client.disconnect()
    assert mock_ecu._connected is False


def test_full_flash_programming_workflow(ecu_client, mock_ecu):
    """Complete flash programming lifecycle: connect, unlock, erase, write
    firmware, verify, reset."""

    ecu_client.connect()

    # 1. Read flash region before programming (should be 0x00 = erased)
    flash_data = _to_bytes(ecu_client.upload(4, FLASH_BASE, 0))
    assert flash_data == b"\x00\x00\x00\x00"

    # 2. Unlock PGM resource
    ecu_client.unlock()

    # 3. Start programming session
    pgm_info = ecu_client.program_start()
    assert pgm_info.max_cto_pgm == 8
    assert mock_ecu._programming is True

    # 4. Erase flash (64 KB)
    ecu_client.program_clear(FLASH_SIZE)
    assert mock_ecu._program_cleared is True

    # 5. Write firmware data at flash base
    firmware_header = b"\x7F\x45\x4C\x46"  # ELF magic (all < 0x80)
    ecu_client.set_mta(FLASH_BASE, 0)
    ecu_client.program(firmware_header, block_length=len(firmware_header))

    # 6. Write more firmware data at next offset
    firmware_body = b"\x01\x02\x03\x04\x05\x06\x07\x08"  # all < 0x80
    ecu_client.set_mta(FLASH_BASE + 4, 0)
    ecu_client.program(firmware_body, block_length=len(firmware_body))

    # 7. Verify programmed data
    header_readback = _to_bytes(ecu_client.upload(4, FLASH_BASE, 0))
    assert header_readback == firmware_header

    body_readback = _to_bytes(ecu_client.upload(8, FLASH_BASE + 4, 0))
    assert body_readback == firmware_body

    # 8. Verify untouched flash area is still erased
    beyond = _to_bytes(ecu_client.upload(4, FLASH_BASE + 12, 0))
    assert beyond == b"\x00\x00\x00\x00"

    # 9. Reset ECU
    ecu_client.program_reset()
    assert mock_ecu._programming is False

    ecu_client.disconnect()


def test_full_daq_configuration_workflow(ecu_client, mock_ecu):
    """Set up DAQ lists to measure engine RPM and coolant temperature,
    start acquisition, then stop and clean up."""

    ecu_client.connect()

    # 1. Query DAQ capabilities
    daq_info = ecu_client.get_daq_info()
    assert daq_info.processor["maxDaq"] >= 4

    # 2. Free any existing DAQ lists
    ecu_client.free_daq()

    # 3. Allocate 2 DAQ lists (one for engine, one for thermal)
    ecu_client.alloc_daq(2)
    assert len(mock_ecu._daq_lists) == 2

    # 4. Configure DAQ list 0 (engine): 1 ODT with 2 entries
    ecu_client.alloc_odt(0, 1)
    assert mock_ecu._daq_lists[0].odt_count == 1
    ecu_client.alloc_odt_entry(0, 0, 2)

    # Entry 0: engine RPM (4 bytes at measurement address)
    rpm_addr = 0x0020_000C
    ecu_client.set_daq_ptr(0, 0, 0)
    ecu_client.write_daq(0xFF, 4, 0, rpm_addr)
    assert len(mock_ecu._daq_lists[0].entries) == 1
    assert mock_ecu._daq_lists[0].entries[0] == (0xFF, 4, 0, rpm_addr)

    # Entry 1: vehicle speed (4 bytes)
    speed_addr = 0x0020_0004
    ecu_client.set_daq_ptr(0, 0, 1)
    ecu_client.write_daq(0xFF, 4, 0, speed_addr)

    # 5. Configure DAQ list 1 (thermal): 1 ODT with 1 entry
    ecu_client.alloc_odt(1, 1)
    ecu_client.alloc_odt_entry(1, 0, 1)

    coolant_addr = 0x0020_0000
    ecu_client.set_daq_ptr(1, 0, 0)
    ecu_client.write_daq(0xFF, 4, 0, coolant_addr)

    # 6. Set DAQ list modes (event channel 1 for engine, 2 for thermal)
    ecu_client.set_daq_list_mode(0x10, 0, 1, 1, 0)
    ecu_client.set_daq_list_mode(0x10, 1, 2, 1, 0)
    assert mock_ecu._daq_lists[0].event_channel == 1
    assert mock_ecu._daq_lists[1].event_channel == 2

    # 7. Start individual DAQ lists
    ecu_client.start_stop_daq_list(1, 0)
    assert mock_ecu._daq_lists[0].running is True
    assert mock_ecu._daq_lists[1].running is False

    ecu_client.start_stop_daq_list(1, 1)
    assert mock_ecu._daq_lists[1].running is True

    # 8. Stop all DAQ lists synchronously
    ecu_client.start_stop_synch(0)
    assert all(not dl.running for dl in mock_ecu._daq_lists)

    # 9. Clean up
    ecu_client.free_daq()
    assert len(mock_ecu._daq_lists) == 0

    ecu_client.disconnect()


# -- Targeted scenario tests ---------------------------------------------------


def test_read_all_calibration_parameters(ecu_client):
    """All pre-populated calibration parameters should be readable."""
    ecu_client.connect()
    for addr, expected in CALIBRATION_MAP.items():
        data = _to_bytes(ecu_client.upload(len(expected), addr, 0))
        assert data == expected, f"Mismatch at 0x{addr:08X}"


def test_read_all_measurement_signals(ecu_client):
    """All pre-populated measurement signals should be readable."""
    ecu_client.connect()
    for addr, expected in MEASUREMENT_MAP.items():
        data = _to_bytes(ecu_client.upload(len(expected), addr, 0))
        assert data == expected, f"Mismatch at 0x{addr:08X}"


def test_calibration_write_without_unlock_succeeds(ecu_client, mock_ecu):
    """The XCP driver doesn't enforce protection at the memory write level
    (unlike UDS where the ECU rejects writes). The mock ECU allows writes
    but protection state is tracked for the test to verify."""
    ecu_client.connect()
    assert mock_ecu._protection["calpag"] is True

    # XCP memory write goes through even without unlock (ECU-specific behavior)
    ecu_client.download(0x0010_0000, b"\x00\x00\x00\x00", 0)
    data = _to_bytes(ecu_client.upload(4, 0x0010_0000, 0))
    assert data == b"\x00\x00\x00\x00"


def test_multiple_calibration_changes(ecu_client):
    """Modify several calibration parameters and verify each independently."""
    ecu_client.connect()
    ecu_client.unlock()

    changes = {
        0x0010_0000: struct.pack("<I", 120),   # max RPM scale
        0x0010_0004: struct.pack("<I", 75),    # idle RPM target
        0x0010_0008: struct.pack("<I", 15),    # fuel trim %
    }

    for addr, new_value in changes.items():
        ecu_client.download(addr, new_value, 0)

    for addr, expected in changes.items():
        data = _to_bytes(ecu_client.upload(len(expected), addr, 0))
        assert data == expected, f"Mismatch at 0x{addr:08X}"


def test_program_clear_before_start_fails(ecu_client):
    """programClear without programStart should fail (sequence error)."""
    ecu_client.connect()
    ecu_client.unlock()

    with pytest.raises(DriverError, match="programStart must be called"):
        ecu_client.program_clear(FLASH_SIZE)


def test_program_without_clear_fails(ecu_client):
    """program without programClear should fail (sequence error)."""
    ecu_client.connect()
    ecu_client.unlock()
    ecu_client.program_start()

    with pytest.raises(DriverError, match="programClear must be called"):
        ecu_client.program(b"\x00" * 8)


def test_operations_before_connect_fail(ecu_client, mock_ecu):
    """Operations called before connect() should raise an error."""
    with pytest.raises(DriverError, match="Not connected"):
        ecu_client.get_id()


def test_reconnect_preserves_memory(ecu_client, mock_ecu):
    """After disconnect + reconnect, calibration writes should persist
    (simulating non-volatile storage)."""
    ecu_client.connect()
    new_val = struct.pack("<I", 42)
    ecu_client.download(0x0010_0000, new_val, 0)
    ecu_client.disconnect()

    ecu_client.connect()
    data = _to_bytes(ecu_client.upload(4, 0x0010_0000, 0))
    assert struct.unpack("<I", data)[0] == 42


def test_checksum_over_measurement_region(ecu_client):
    """Checksum over a measurement region should reflect stored values."""
    ecu_client.connect()
    coolant_addr = 0x0020_0000
    ecu_client.set_mta(coolant_addr, 0)
    csum = ecu_client.build_checksum(4)
    expected = sum(MEASUREMENT_MAP[coolant_addr])
    assert csum.checksum_value == expected


def test_session_status_after_unlock(ecu_client):
    """After unlock, all protection bits should be cleared."""
    ecu_client.connect()
    prot = ecu_client.unlock()
    for resource, locked in prot.items():
        assert locked is False, f"{resource} should be unlocked"


def test_flash_readback_after_partial_program(ecu_client):
    """Program only the first 8 bytes of flash, verify rest is untouched."""
    ecu_client.connect()
    ecu_client.unlock()
    ecu_client.program_start()
    ecu_client.program_clear(FLASH_SIZE)

    firmware = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    ecu_client.set_mta(FLASH_BASE, 0)
    ecu_client.program(firmware, block_length=len(firmware))

    # Programmed region
    readback = _to_bytes(ecu_client.upload(8, FLASH_BASE, 0))
    assert readback == firmware

    # Adjacent erased region
    erased = _to_bytes(ecu_client.upload(4, FLASH_BASE + 8, 0))
    assert erased == b"\x00\x00\x00\x00"

    ecu_client.program_reset()
