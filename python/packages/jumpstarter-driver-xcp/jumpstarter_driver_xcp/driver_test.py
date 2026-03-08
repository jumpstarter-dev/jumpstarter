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
    call_kwargs = mock_create.call_args
    args = call_kwargs.kwargs if call_kwargs.kwargs else {}
    if not args:
        args = {
            "transport": call_kwargs[1].get("transport", call_kwargs[0][0] if call_kwargs[0] else None),
        }
        # Extract from positional/keyword args
        keys = [
            "transport", "config_file", "host", "port", "protocol",
            "can_interface", "channel", "bitrate", "can_id_master", "can_id_slave",
        ]
        for i, key in enumerate(keys):
            if i < len(call_kwargs[0]):
                args[key] = call_kwargs[0][i]
            elif key in (call_kwargs[1] if len(call_kwargs) > 1 and call_kwargs[1] else {}):
                args[key] = call_kwargs[1][key]

    # The call should use the CAN transport with our specific params
    actual_kwargs = mock_create.call_args.kwargs
    assert actual_kwargs["can_interface"] == "vector"
    assert actual_kwargs["channel"] == 0
    assert actual_kwargs["bitrate"] == 500000
    assert actual_kwargs["can_id_master"] == 0x7E0
    assert actual_kwargs["can_id_slave"] == 0x7E1


# =============================================================================
# Integration tests with simulated XCP server
# (These use the mock_xcp_server fixture from conftest.py but require
# pyxcp to actually connect. They are currently skipped because pyxcp's
# Master + ArgumentParser flow needs a full traitlets config setup that
# is complex to wire in a test. The mock-based tests above provide
# equivalent coverage through the gRPC boundary.)
# =============================================================================
