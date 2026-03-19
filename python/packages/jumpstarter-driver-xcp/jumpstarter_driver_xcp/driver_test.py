from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from .driver import Xcp
from jumpstarter.client.core import DriverError
from jumpstarter.common.utils import serve


def _make_mock_master():
    """Create a mock pyxcp Master with realistic return values."""
    master = MagicMock()

    master.slaveProperties = {
        "maxCto": 8,
        "maxDto": 8,
        "byteOrder": "INTEL",
        "supportsPgm": True,
        "supportsStim": False,
        "supportsDaq": True,
        "supportsCalpag": True,
        "protocolLayerVersion": 1,
        "transportLayerVersion": 1,
        "addressGranularity": "BYTE",
        "slaveBlockMode": False,
    }

    master.connect.return_value = MagicMock()
    master.identifier.return_value = "XCP_TEST_SLAVE_v1.0"
    master.shortUpload.return_value = b"\x01\x02\x03\x04"
    master.buildChecksum.return_value = MagicMock(checksumType=1, checksum=0xDEAD)
    master.getDaqInfo.return_value = {
        "processor": {"minDaq": 0, "maxDaq": 4},
        "resolution": {"timestampTicks": 1},
        "channels": [],
    }
    master.programStart.return_value = MagicMock(
        commModePgm=0, maxCtoPgm=8, maxBsPgm=0, minStPgm=0, queueSizePgm=0,
    )

    status_mock = MagicMock()
    status_mock.items.return_value = [("store_cal_req", False)]
    master.getStatus.return_value = status_mock
    master.getCurrentProtectionStatus.return_value = {
        "dbg": False, "pgm": False, "stim": False, "daq": False, "calpag": False,
    }

    return master


@pytest.fixture
def mock_master():
    return _make_mock_master()


@pytest.fixture
def client(mock_master):
    instance = Xcp(
        transport="ETH",
        host="127.0.0.1",
        port=5555,
        protocol="TCP",
    )

    with patch(
        "jumpstarter_driver_xcp.driver._create_xcp_master",
        return_value=mock_master,
    ):
        with serve(instance) as client:
            yield client


# =============================================================================
# Happy-path tests
# =============================================================================


def test_connect(client, mock_master):
    info = client.connect()
    assert info.max_cto == 8
    assert info.max_dto == 8
    assert info.byte_order == "INTEL"
    assert info.supports_daq is True
    assert info.supports_calpag is True
    assert info.supports_pgm is True
    assert info.supports_stim is False
    mock_master.connect.assert_called_once_with(0)


def test_connect_with_mode(client, mock_master):
    client.connect(mode=1)
    mock_master.connect.assert_called_once_with(1)


def test_disconnect(client, mock_master):
    client.connect()
    client.disconnect()
    mock_master.close.assert_called_once()


def test_get_id(client, mock_master):
    client.connect()
    result = client.get_id(1)
    assert result.identifier == "XCP_TEST_SLAVE_v1.0"
    assert result.id_type == 1
    mock_master.identifier.assert_called_once_with(1)


def test_get_id_custom_type(client, mock_master):
    client.connect()
    client.get_id(0)
    mock_master.identifier.assert_called_once_with(0)


def test_get_status(client, mock_master):
    client.connect()
    status = client.get_status()
    assert isinstance(status.resource_protection, dict)
    assert status.resource_protection["pgm"] is False
    assert status.resource_protection["daq"] is False
    mock_master.getStatus.assert_called_once()
    mock_master.getCurrentProtectionStatus.assert_called()


def test_upload(client, mock_master):
    client.connect()
    data = client.upload(4, 0x1000, 0)
    assert bytes(data, "latin-1") if isinstance(data, str) else data == b"\x01\x02\x03\x04"
    mock_master.shortUpload.assert_called_once_with(4, 0x1000, 0)


def test_download(client, mock_master):
    client.connect()
    client.download(0x2000, b"\x01\x02", 0)
    mock_master.setMta.assert_called_once_with(0x2000, 0)
    mock_master.download.assert_called_once_with(b"\x01\x02")


def test_set_mta(client, mock_master):
    client.connect()
    client.set_mta(0x3000, 1)
    mock_master.setMta.assert_called_once_with(0x3000, 1)


def test_build_checksum(client, mock_master):
    client.connect()
    client.set_mta(0x1000, 0)
    result = client.build_checksum(256)
    assert result.checksum_type == 1
    assert result.checksum_value == 0xDEAD
    mock_master.buildChecksum.assert_called_once_with(256)


def test_get_daq_info(client, mock_master):
    client.connect()
    info = client.get_daq_info()
    assert info.processor["minDaq"] == 0
    assert info.processor["maxDaq"] == 4
    assert info.resolution["timestampTicks"] == 1
    assert info.channels == []
    mock_master.getDaqInfo.assert_called_once()


def test_free_daq(client, mock_master):
    client.connect()
    client.free_daq()
    mock_master.freeDaq.assert_called_once()


def test_alloc_daq(client, mock_master):
    client.connect()
    client.alloc_daq(2)
    mock_master.allocDaq.assert_called_once_with(2)


def test_alloc_odt(client, mock_master):
    client.connect()
    client.alloc_odt(0, 3)
    mock_master.allocOdt.assert_called_once_with(0, 3)


def test_alloc_odt_entry(client, mock_master):
    client.connect()
    client.alloc_odt_entry(0, 1, 4)
    mock_master.allocOdtEntry.assert_called_once_with(0, 1, 4)


def test_set_daq_ptr(client, mock_master):
    client.connect()
    client.set_daq_ptr(0, 0, 0)
    mock_master.setDaqPtr.assert_called_once_with(0, 0, 0)


def test_write_daq(client, mock_master):
    client.connect()
    client.write_daq(0xFF, 4, 0, 0x1000)
    mock_master.writeDaq.assert_called_once_with(0xFF, 4, 0, 0x1000)


def test_set_daq_list_mode(client, mock_master):
    client.connect()
    client.set_daq_list_mode(0x10, 0, 1, 1, 0)
    mock_master.setDaqListMode.assert_called_once_with(0x10, 0, 1, 1, 0)


def test_start_stop_daq_list(client, mock_master):
    client.connect()
    client.start_stop_daq_list(1, 0)
    mock_master.startStopDaqList.assert_called_once_with(1, 0)


def test_start_stop_synch(client, mock_master):
    client.connect()
    client.start_stop_synch(1)
    mock_master.startStopSynch.assert_called_once_with(1)


def test_program_start(client, mock_master):
    client.connect()
    info = client.program_start()
    assert info.max_cto_pgm == 8
    assert info.comm_mode_pgm == 0
    mock_master.programStart.assert_called_once()


def test_program_clear(client, mock_master):
    client.connect()
    client.program_clear(0x10000, mode=0)
    mock_master.programClear.assert_called_once_with(0, 0x10000)


def test_program(client, mock_master):
    client.connect()
    data = b"\x00" * 64
    client.program(data, block_length=64)
    mock_master.program.assert_called_once_with(data, 64)


def test_program_reset(client, mock_master):
    client.connect()
    client.program_reset()
    mock_master.programReset.assert_called_once()


def test_program_full_flow(client, mock_master):
    """Test a complete programming sequence end-to-end."""
    client.connect()
    client.program_start()
    client.program_clear(0x10000)
    client.program(b"\x00" * 64)
    client.program_reset()
    mock_master.programStart.assert_called_once()
    mock_master.programClear.assert_called_once()
    mock_master.program.assert_called_once()
    mock_master.programReset.assert_called_once()


def test_unlock(client, mock_master):
    client.connect()
    result = client.unlock()
    assert isinstance(result, dict)
    assert "pgm" in result
    assert result["pgm"] is False
    mock_master.cond_unlock.assert_called_once_with(None)


def test_unlock_with_resources(client, mock_master):
    client.connect()
    client.unlock(resources=["pgm", "daq"])
    mock_master.cond_unlock.assert_called_once_with(["pgm", "daq"])


# =============================================================================
# Error-path tests
# =============================================================================


def test_connect_timeout(mock_master):
    mock_master.connect.side_effect = TimeoutError("No response from ECU")

    instance = Xcp(transport="ETH", host="127.0.0.1", port=5555, protocol="TCP")
    with patch(
        "jumpstarter_driver_xcp.driver._create_xcp_master",
        return_value=mock_master,
    ):
        with serve(instance) as client:
            with pytest.raises(DriverError, match="No response from ECU"):
                client.connect()


def test_upload_error(mock_master):
    mock_master.shortUpload.side_effect = RuntimeError("XCP ERR_ACCESS_DENIED")

    instance = Xcp(transport="ETH", host="127.0.0.1", port=5555, protocol="TCP")
    with patch(
        "jumpstarter_driver_xcp.driver._create_xcp_master",
        return_value=mock_master,
    ):
        with serve(instance) as client:
            client.connect()
            with pytest.raises(DriverError, match="ERR_ACCESS_DENIED"):
                client.upload(4, 0x1000, 0)


def test_download_error(mock_master):
    mock_master.download.side_effect = RuntimeError("XCP ERR_OUT_OF_RANGE")

    instance = Xcp(transport="ETH", host="127.0.0.1", port=5555, protocol="TCP")
    with patch(
        "jumpstarter_driver_xcp.driver._create_xcp_master",
        return_value=mock_master,
    ):
        with serve(instance) as client:
            client.connect()
            with pytest.raises(DriverError, match="ERR_OUT_OF_RANGE"):
                client.download(0x2000, b"\x01\x02", 0)


def test_program_clear_error(mock_master):
    mock_master.programClear.side_effect = RuntimeError("Erase failed")

    instance = Xcp(transport="ETH", host="127.0.0.1", port=5555, protocol="TCP")
    with patch(
        "jumpstarter_driver_xcp.driver._create_xcp_master",
        return_value=mock_master,
    ):
        with serve(instance) as client:
            client.connect()
            with pytest.raises(DriverError, match="Erase failed"):
                client.program_clear(0x10000)


def test_unlock_error(mock_master):
    mock_master.cond_unlock.side_effect = RuntimeError("Seed & key failed")

    instance = Xcp(transport="ETH", host="127.0.0.1", port=5555, protocol="TCP")
    with patch(
        "jumpstarter_driver_xcp.driver._create_xcp_master",
        return_value=mock_master,
    ):
        with serve(instance) as client:
            client.connect()
            with pytest.raises(DriverError, match="Seed & key failed"):
                client.unlock()


def test_get_daq_info_error(mock_master):
    mock_master.getDaqInfo.side_effect = RuntimeError("DAQ not supported")

    instance = Xcp(transport="ETH", host="127.0.0.1", port=5555, protocol="TCP")
    with patch(
        "jumpstarter_driver_xcp.driver._create_xcp_master",
        return_value=mock_master,
    ):
        with serve(instance) as client:
            client.connect()
            with pytest.raises(DriverError, match="DAQ not supported"):
                client.get_daq_info()


# =============================================================================
# Config validation tests
# =============================================================================


def test_invalid_transport_type():
    with pytest.raises(ValidationError):
        Xcp(transport="INVALID", host="127.0.0.1", port=5555)


def test_invalid_protocol_type():
    with pytest.raises(ValidationError):
        Xcp(transport="ETH", host="127.0.0.1", port=5555, protocol="INVALID")


def test_invalid_port_type():
    with pytest.raises(ValidationError):
        Xcp(transport="ETH", host="127.0.0.1", port="not_a_port")


def test_default_config_values():
    instance = Xcp()
    assert instance.transport.value == "ETH"
    assert instance.host == "localhost"
    assert instance.port == 5555
    assert instance.protocol.value == "TCP"
    assert instance.can_interface is None
    assert instance.channel is None
    assert instance.bitrate is None
    assert instance.config_file is None


@patch("jumpstarter_driver_xcp.driver._create_xcp_master")
def test_custom_config_forwarded(mock_create):
    """Verify non-default config values are used to create the master."""
    mock_create.return_value = _make_mock_master()

    instance = Xcp(
        transport="CAN",
        can_interface="vector",
        channel=0,
        bitrate=500000,
        can_id_master=0x7E0,
        can_id_slave=0x7E1,
    )
    with serve(instance) as client:
        client.connect()

    mock_create.assert_called_once()
    actual_kwargs = mock_create.call_args.kwargs
    assert actual_kwargs["can_interface"] == "vector"
    assert actual_kwargs["channel"] == 0
    assert actual_kwargs["bitrate"] == 500000
    assert actual_kwargs["can_id_master"] == 0x7E0
    assert actual_kwargs["can_id_slave"] == 0x7E1


# =============================================================================
# Stateful integration tests
#
# These use a StatefulXcpMaster (conftest.py) that behaves like a real
# XCP ECU: it tracks connection state, memory, MTA pointer, DAQ
# allocation, and programming sequence.  Each test exercises a realistic
# multi-step workflow through the full gRPC boundary.
# =============================================================================


def _stateful_client_ctx(stateful_master):
    """Context manager helper: serve() an Xcp driver backed by the stateful mock."""
    instance = Xcp(transport="ETH", host="127.0.0.1", port=5555, protocol="TCP")
    with patch(
        "jumpstarter_driver_xcp.driver._create_xcp_master",
        return_value=stateful_master,
    ):
        with serve(instance) as c:
            yield c


@pytest.fixture
def stateful_client(stateful_master):
    yield from _stateful_client_ctx(stateful_master)


# -- session & identification --------------------------------------------------


def test_stateful_connect_disconnect(stateful_client, stateful_master):
    info = stateful_client.connect()
    assert info.max_cto == 8
    assert info.max_dto == 256
    assert info.byte_order == "INTEL"
    assert stateful_master._connected is True

    stateful_client.disconnect()
    assert stateful_master._connected is False


def test_stateful_get_id_after_connect(stateful_client):
    stateful_client.connect()
    result = stateful_client.get_id(1)
    assert result.identifier == "XCP_STATEFUL_SIM_v2.0"


def test_stateful_get_status_shows_protection(stateful_client):
    stateful_client.connect()
    status = stateful_client.get_status()
    assert status.resource_protection["pgm"] is True
    assert status.resource_protection["calpag"] is True
    assert status.resource_protection["daq"] is False


# -- unlock flow ---------------------------------------------------------------


def test_stateful_unlock_clears_protection(stateful_client):
    stateful_client.connect()
    result = stateful_client.unlock()
    assert result["pgm"] is False
    assert result["calpag"] is False
    assert result["dbg"] is False


# -- memory read / write round-trip -------------------------------------------


def test_stateful_download_then_upload(stateful_client):
    """Write data to an address and read it back — verifies memory state."""
    stateful_client.connect()
    stateful_client.download(0x1000, b"\x0C\x0A", 0)

    data = stateful_client.upload(2, 0x1000, 0)
    raw = bytes(data, "latin-1") if isinstance(data, str) else data
    assert raw == b"\x0C\x0A"


def test_stateful_upload_unwritten_address_returns_zeros(stateful_client):
    stateful_client.connect()
    data = stateful_client.upload(4, 0x9999, 0)
    raw = bytes(data, "latin-1") if isinstance(data, str) else data
    assert raw == b"\x00\x00\x00\x00"


def test_stateful_overwrite_memory(stateful_client):
    """Download twice to the same address — second write wins."""
    stateful_client.connect()
    stateful_client.download(0x2000, b"\x11\x22", 0)
    stateful_client.download(0x2000, b"\x33\x44", 0)

    data = stateful_client.upload(2, 0x2000, 0)
    raw = bytes(data, "latin-1") if isinstance(data, str) else data
    assert raw == b"\x33\x44"


def test_stateful_multiple_addresses(stateful_client):
    """Write to different addresses and verify each independently."""
    stateful_client.connect()
    stateful_client.download(0x1000, b"\x01", 0)
    stateful_client.download(0x2000, b"\x02", 0)
    stateful_client.download(0x3000, b"\x03", 0)

    for addr, expected in [(0x1000, b"\x01"), (0x2000, b"\x02"), (0x3000, b"\x03")]:
        data = stateful_client.upload(1, addr, 0)
        raw = bytes(data, "latin-1") if isinstance(data, str) else data
        assert raw == expected, f"Mismatch at 0x{addr:X}"


# -- checksum ------------------------------------------------------------------


def test_stateful_checksum_over_written_data(stateful_client):
    stateful_client.connect()
    stateful_client.download(0x4000, b"\x01\x02\x03\x04", 0)

    stateful_client.set_mta(0x4000, 0)
    result = stateful_client.build_checksum(4)
    assert result.checksum_type == 1
    assert result.checksum_value == 0x01 + 0x02 + 0x03 + 0x04


# -- DAQ allocation workflow ---------------------------------------------------


def test_stateful_daq_alloc_flow(stateful_client, stateful_master):
    stateful_client.connect()

    info = stateful_client.get_daq_info()
    assert info.processor["maxDaq"] >= 4

    stateful_client.free_daq()
    assert stateful_master._daq_lists == 0

    stateful_client.alloc_daq(3)
    assert stateful_master._daq_lists == 3

    stateful_client.alloc_odt(0, 2)
    stateful_client.alloc_odt_entry(0, 0, 4)

    stateful_client.set_daq_ptr(0, 0, 0)
    assert stateful_master._daq_ptr == (0, 0, 0)

    stateful_client.write_daq(0xFF, 4, 0, 0x1000)
    stateful_client.set_daq_list_mode(0x10, 0, 1, 1, 0)
    stateful_client.start_stop_daq_list(1, 0)
    stateful_client.start_stop_synch(1)

    stateful_client.start_stop_synch(0)
    stateful_client.free_daq()
    assert stateful_master._daq_lists == 0


# -- programming sequence -----------------------------------------------------


def test_stateful_full_programming_flow(stateful_client, stateful_master):
    """Exercise the complete flash-programming lifecycle."""
    stateful_client.connect()

    # Pre-load memory that will be cleared
    stateful_client.download(0x0000, b"\x7F" * 16, 0)

    info = stateful_client.program_start()
    assert info.max_cto_pgm == 8
    assert stateful_master._programming is True

    stateful_client.program_clear(0x10000)
    assert stateful_master._program_cleared is True
    # Memory below clear_range should be wiped
    data = stateful_client.upload(4, 0x0000, 0)
    raw = bytes(data, "latin-1") if isinstance(data, str) else data
    assert raw == b"\x00\x00\x00\x00"

    # Program new data
    stateful_client.set_mta(0x0000, 0)
    stateful_client.program(b"\x0D\x0A", block_length=2)

    # Verify programmed data is in memory
    data = stateful_client.upload(2, 0x0000, 0)
    raw = bytes(data, "latin-1") if isinstance(data, str) else data
    assert raw == b"\x0D\x0A"

    stateful_client.program_reset()
    assert stateful_master._programming is False

    stateful_client.disconnect()
    assert stateful_master._connected is False


def test_stateful_program_clear_before_start_raises(stateful_master):
    """programClear without programStart should fail."""
    instance = Xcp(transport="ETH", host="127.0.0.1", port=5555, protocol="TCP")
    with patch(
        "jumpstarter_driver_xcp.driver._create_xcp_master",
        return_value=stateful_master,
    ):
        with serve(instance) as c:
            c.connect()
            with pytest.raises(DriverError, match="programStart must be called"):
                c.program_clear(0x10000)


def test_stateful_program_before_clear_raises(stateful_master):
    """program without programClear should fail."""
    instance = Xcp(transport="ETH", host="127.0.0.1", port=5555, protocol="TCP")
    with patch(
        "jumpstarter_driver_xcp.driver._create_xcp_master",
        return_value=stateful_master,
    ):
        with serve(instance) as c:
            c.connect()
            c.program_start()
            with pytest.raises(DriverError, match="programClear must be called"):
                c.program(b"\x00" * 8)


# -- end-to-end calibration workflow ------------------------------------------


def test_stateful_calibration_workflow(stateful_client):
    """Simulate a typical calibration session: connect, unlock, read,
    modify, write-back, verify, disconnect."""
    stateful_client.connect()
    stateful_client.unlock()

    # Initial state: zeros
    orig = stateful_client.upload(4, 0x5000, 0)
    raw_orig = bytes(orig, "latin-1") if isinstance(orig, str) else orig
    assert raw_orig == b"\x00\x00\x00\x00"

    # Calibrate: write a new parameter value
    stateful_client.download(0x5000, b"\x42\x00\x00\x00", 0)

    # Read back and verify
    modified = stateful_client.upload(4, 0x5000, 0)
    raw_mod = bytes(modified, "latin-1") if isinstance(modified, str) else modified
    assert raw_mod == b"\x42\x00\x00\x00"

    # Checksum verification
    stateful_client.set_mta(0x5000, 0)
    csum = stateful_client.build_checksum(4)
    assert csum.checksum_value == 0x42

    stateful_client.disconnect()


# -- connect-required enforcement ---------------------------------------------


def test_stateful_operations_before_connect_raise(stateful_master):
    """Methods called before connect() should fail."""
    instance = Xcp(transport="ETH", host="127.0.0.1", port=5555, protocol="TCP")
    with patch(
        "jumpstarter_driver_xcp.driver._create_xcp_master",
        return_value=stateful_master,
    ):
        with serve(instance) as c:
            with pytest.raises(DriverError, match="Not connected"):
                c.get_id()


def test_stateful_reconnect_after_disconnect(stateful_client, stateful_master):
    """After disconnect, a new connect should succeed and reset state."""
    stateful_client.connect()
    stateful_client.download(0x7000, b"\x7F", 0)
    stateful_client.disconnect()
    assert stateful_master._connected is False

    stateful_client.connect()
    assert stateful_master._connected is True

    # Memory persists across reconnect (simulates non-volatile storage)
    data = stateful_client.upload(1, 0x7000, 0)
    raw = bytes(data, "latin-1") if isinstance(data, str) else data
    assert raw == b"\x7F"
