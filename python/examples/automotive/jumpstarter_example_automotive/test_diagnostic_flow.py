"""Representative end-to-end diagnostic test using jumpstarter.

Demonstrates a realistic ECU diagnostic workflow: session management,
DID read/write, DTC handling, security access, and ECU reset -- all
running through the full jumpstarter driver/gRPC/client pipeline against
a stateful mock ECU.
"""

from jumpstarter_driver_uds.common import UdsResetType, UdsSessionType

from .mock_ecu import INITIAL_DIDS, INITIAL_DTCS, derive_key


def test_full_diagnostic_workflow(ecu_client):
    """Complete diagnostic session exercising the major UDS services."""

    # 1. Read VIN in default session (always allowed)
    values = ecu_client.read_data_by_identifier([0xF190])
    assert len(values) == 1
    assert values[0].did == 0xF190
    assert values[0].value == INITIAL_DIDS[0xF190].hex()

    # 2. Attempt to write DID in default session (should fail)
    resp = ecu_client.write_data_by_identifier(0xF190, b"NEWVIN12345678901")
    assert resp.success is False
    assert resp.nrc == 0x22

    # 3. Switch to extended diagnostic session
    resp = ecu_client.change_session(UdsSessionType.EXTENDED)
    assert resp.success is True
    assert resp.service == "DiagnosticSessionControl"

    # 4. Read DTCs -- mock ECU has pre-populated faults
    dtcs = ecu_client.read_dtc_by_status_mask(0xFF)
    assert len(dtcs) == len(INITIAL_DTCS)
    initial_ids = {dtc_id for dtc_id, _ in INITIAL_DTCS}
    assert {d.dtc_id for d in dtcs} == initial_ids

    # 5. Clear DTCs
    resp = ecu_client.clear_dtc()
    assert resp.success is True

    # 6. Verify DTCs are cleared
    dtcs = ecu_client.read_dtc_by_status_mask(0xFF)
    assert len(dtcs) == 0

    # 7. Unlock security access (level 1)
    seed_resp = ecu_client.request_seed(1)
    assert seed_resp.success is True
    assert len(seed_resp.seed) == 8  # 4 bytes as hex

    key = derive_key(bytes.fromhex(seed_resp.seed))
    resp = ecu_client.send_key(1, key)
    assert resp.success is True
    assert resp.service == "SecurityAccess"

    # 8. Write a DID (requires extended session + security unlock)
    new_vin = b"NEWVIN12345678901"
    resp = ecu_client.write_data_by_identifier(0xF190, new_vin)
    assert resp.success is True

    # 9. Read back the written value
    values = ecu_client.read_data_by_identifier([0xF190])
    assert len(values) == 1
    assert values[0].value == new_vin.hex()

    # 10. ECU reset
    resp = ecu_client.ecu_reset(UdsResetType.HARD)
    assert resp.success is True

    # 11. After reset: session reverts to default, DTCs restored, written DID persists (NVM)
    values = ecu_client.read_data_by_identifier([0xF190])
    assert values[0].value == new_vin.hex()

    dtcs = ecu_client.read_dtc_by_status_mask(0xFF)
    assert len(dtcs) == len(INITIAL_DTCS)


def test_security_wrong_key(ecu_client):
    """Sending the wrong security key returns NRC 0x35 (invalidKey)."""
    ecu_client.change_session(UdsSessionType.EXTENDED)

    seed_resp = ecu_client.request_seed(1)
    assert seed_resp.success is True

    wrong_key = b"\x00\x00\x00\x00"
    resp = ecu_client.send_key(1, wrong_key)
    assert resp.success is False
    assert resp.nrc == 0x35


def test_write_without_security_unlock(ecu_client):
    """Writing a DID in extended session without unlocking security fails."""
    ecu_client.change_session(UdsSessionType.EXTENDED)

    resp = ecu_client.write_data_by_identifier(0xF190, b"SHOULDFAIL0000000")
    assert resp.success is False
    assert resp.nrc == 0x22


def test_read_unknown_did(ecu_client):
    """Reading a non-existent DID returns an empty list (NRC logged by driver)."""
    values = ecu_client.read_data_by_identifier([0xBEEF])
    assert len(values) == 0


def test_session_transitions(ecu_client):
    """Verify session transitions: default -> extended -> programming -> default."""
    resp = ecu_client.change_session(UdsSessionType.EXTENDED)
    assert resp.success is True

    resp = ecu_client.change_session(UdsSessionType.PROGRAMMING)
    assert resp.success is True

    resp = ecu_client.change_session(UdsSessionType.DEFAULT)
    assert resp.success is True


def test_tester_present(ecu_client):
    """TesterPresent should succeed without error."""
    ecu_client.tester_present()


def test_read_all_initial_dids(ecu_client):
    """All pre-populated DIDs should be readable in default session."""
    for did, expected_value in INITIAL_DIDS.items():
        values = ecu_client.read_data_by_identifier([did])
        assert len(values) == 1
        assert values[0].did == did
        assert values[0].value == expected_value.hex()


def test_security_access_in_default_session(ecu_client):
    """Security access should fail in default session (conditionsNotCorrect)."""
    seed_resp = ecu_client.request_seed(1)
    assert seed_resp.success is False
    assert seed_resp.nrc == 0x22
