from unittest.mock import MagicMock, patch

import pytest
from jumpstarter_driver_uds.common import UdsResetType, UdsSessionType
from pydantic import ValidationError
from udsoncan.exceptions import NegativeResponseException

from .driver import UdsDoip
from jumpstarter.client.core import DriverError
from jumpstarter.common.utils import serve


def _make_mock_uds_response(positive=True, data=None):
    resp = MagicMock()
    resp.positive = positive
    resp.data = data
    return resp


def _make_mocks():
    doip_mock = MagicMock()
    conn_mock = MagicMock()
    uds_mock = MagicMock()
    return doip_mock, conn_mock, uds_mock


@patch("jumpstarter_driver_uds_doip.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClientUDSConnector")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClient")
def test_uds_doip_change_session(mock_doip_cls, mock_conn_cls, mock_uds_cls):
    doip_mock, conn_mock, uds_mock = _make_mocks()
    mock_doip_cls.return_value = doip_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    uds_mock.change_session.return_value = _make_mock_uds_response()

    driver = UdsDoip(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        resp = client.change_session(UdsSessionType.EXTENDED)
        assert resp.success is True
        assert resp.service == "DiagnosticSessionControl"


@patch("jumpstarter_driver_uds_doip.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClientUDSConnector")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClient")
def test_uds_doip_ecu_reset(mock_doip_cls, mock_conn_cls, mock_uds_cls):
    doip_mock, conn_mock, uds_mock = _make_mocks()
    mock_doip_cls.return_value = doip_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    uds_mock.ecu_reset.return_value = _make_mock_uds_response()

    driver = UdsDoip(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        resp = client.ecu_reset(UdsResetType.HARD)
        assert resp.success is True
        assert resp.service == "ECUReset"


@patch("jumpstarter_driver_uds_doip.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClientUDSConnector")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClient")
def test_uds_doip_tester_present(mock_doip_cls, mock_conn_cls, mock_uds_cls):
    doip_mock, conn_mock, uds_mock = _make_mocks()
    mock_doip_cls.return_value = doip_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    driver = UdsDoip(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        client.tester_present()
        uds_mock.tester_present.assert_called_once()


@patch("jumpstarter_driver_uds_doip.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClientUDSConnector")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClient")
def test_uds_doip_read_data_by_identifier(mock_doip_cls, mock_conn_cls, mock_uds_cls):
    doip_mock, conn_mock, uds_mock = _make_mocks()
    mock_doip_cls.return_value = doip_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    resp = _make_mock_uds_response()
    resp.service_data = MagicMock()
    resp.service_data.values = {0xF190: b"WDB1234567890ABCD"}
    uds_mock.read_data_by_identifier.return_value = resp

    driver = UdsDoip(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        values = client.read_data_by_identifier([0xF190])
        assert len(values) == 1
        assert values[0].did == 0xF190


@patch("jumpstarter_driver_uds_doip.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClientUDSConnector")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClient")
def test_uds_doip_write_data_by_identifier(mock_doip_cls, mock_conn_cls, mock_uds_cls):
    doip_mock, conn_mock, uds_mock = _make_mocks()
    mock_doip_cls.return_value = doip_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    uds_mock.write_data_by_identifier.return_value = _make_mock_uds_response()

    driver = UdsDoip(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        resp = client.write_data_by_identifier(0xF190, b"ABC123456789")
        assert resp.success is True


@patch("jumpstarter_driver_uds_doip.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClientUDSConnector")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClient")
def test_uds_doip_request_seed(mock_doip_cls, mock_conn_cls, mock_uds_cls):
    doip_mock, conn_mock, uds_mock = _make_mocks()
    mock_doip_cls.return_value = doip_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    resp = _make_mock_uds_response()
    resp.service_data = MagicMock()
    resp.service_data.seed = b"\x01\x02\x03\x04"
    uds_mock.request_seed.return_value = resp

    driver = UdsDoip(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        seed_resp = client.request_seed(1)
        assert seed_resp.seed == "01020304"


@patch("jumpstarter_driver_uds_doip.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClientUDSConnector")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClient")
def test_uds_doip_send_key(mock_doip_cls, mock_conn_cls, mock_uds_cls):
    doip_mock, conn_mock, uds_mock = _make_mocks()
    mock_doip_cls.return_value = doip_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    uds_mock.send_key.return_value = _make_mock_uds_response()

    driver = UdsDoip(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        resp = client.send_key(1, b"\xAA\xBB\xCC\xDD")
        assert resp.success is True
        assert resp.service == "SecurityAccess"


@patch("jumpstarter_driver_uds_doip.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClientUDSConnector")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClient")
def test_uds_doip_clear_dtc(mock_doip_cls, mock_conn_cls, mock_uds_cls):
    doip_mock, conn_mock, uds_mock = _make_mocks()
    mock_doip_cls.return_value = doip_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    uds_mock.clear_dtc.return_value = _make_mock_uds_response()

    driver = UdsDoip(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        resp = client.clear_dtc()
        assert resp.success is True
        assert resp.service == "ClearDiagnosticInformation"


@patch("jumpstarter_driver_uds_doip.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClientUDSConnector")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClient")
def test_uds_doip_read_dtc(mock_doip_cls, mock_conn_cls, mock_uds_cls):
    doip_mock, conn_mock, uds_mock = _make_mocks()
    mock_doip_cls.return_value = doip_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    dtc1 = MagicMock()
    dtc1.id = 0x123456
    dtc1.status = MagicMock()
    dtc1.status.get_byte_as_int.return_value = 0x2F
    dtc1.severity = None

    resp = _make_mock_uds_response()
    resp.service_data = MagicMock()
    resp.service_data.dtcs = [dtc1]
    uds_mock.get_dtc_by_status_mask.return_value = resp

    driver = UdsDoip(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        dtcs = client.read_dtc_by_status_mask(0xFF)
        assert len(dtcs) == 1
        assert dtcs[0].dtc_id == 0x123456
        assert dtcs[0].status == 0x2F


# --- Error path tests ---


@patch("jumpstarter_driver_uds_doip.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClientUDSConnector")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClient")
def test_uds_doip_connection_failure(mock_doip_cls, _mock_conn_cls, _mock_uds_cls):
    mock_doip_cls.side_effect = ConnectionRefusedError("Connection refused")
    with pytest.raises(ConnectionRefusedError, match="Connection refused"):
        UdsDoip(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)


@patch("jumpstarter_driver_uds_doip.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClientUDSConnector")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClient")
def test_uds_doip_timeout_on_change_session(mock_doip_cls, mock_conn_cls, mock_uds_cls):
    doip_mock, conn_mock, uds_mock = _make_mocks()
    mock_doip_cls.return_value = doip_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    uds_mock.change_session.side_effect = TimeoutError("Request timed out")

    driver = UdsDoip(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        with pytest.raises(DriverError, match="Request timed out"):
            client.change_session(UdsSessionType.EXTENDED)


@patch("jumpstarter_driver_uds_doip.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClientUDSConnector")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClient")
def test_uds_doip_nrc_on_ecu_reset(mock_doip_cls, mock_conn_cls, mock_uds_cls):
    doip_mock, conn_mock, uds_mock = _make_mocks()
    mock_doip_cls.return_value = doip_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    nrc_response = MagicMock()
    nrc_response.code = 0x22
    nrc_response.code_name = "conditionsNotCorrect"
    nrc_response.valid = True
    nrc_response.service = MagicMock()
    nrc_response.service.response_id.return_value = 0x51
    uds_mock.ecu_reset.side_effect = NegativeResponseException(nrc_response)

    driver = UdsDoip(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        resp = client.ecu_reset(UdsResetType.HARD)
        assert resp.success is False
        assert resp.nrc == 0x22
        assert resp.nrc_name == "conditionsNotCorrect"


@patch("jumpstarter_driver_uds_doip.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClientUDSConnector")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClient")
def test_uds_doip_close_resources(mock_doip_cls, mock_conn_cls, mock_uds_cls):
    """Verify close() shuts down both UDS and DoIP clients."""
    doip_mock, conn_mock, uds_mock = _make_mocks()
    mock_doip_cls.return_value = doip_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    driver = UdsDoip(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    driver.close()
    uds_mock.close.assert_called_once()
    doip_mock.close.assert_called_once()


# --- Config validation tests ---


def test_uds_doip_missing_required_ecu_ip():
    with pytest.raises(ValidationError, match="ecu_ip"):
        UdsDoip(ecu_logical_address=0x00E0)  # ty: ignore[missing-argument]


def test_uds_doip_missing_required_ecu_logical_address():
    with pytest.raises(ValidationError, match="ecu_logical_address"):
        UdsDoip(ecu_ip="192.168.1.100")  # ty: ignore[missing-argument]


def test_uds_doip_invalid_ecu_ip_type():
    with pytest.raises(ValidationError):
        UdsDoip(ecu_ip=12345, ecu_logical_address=0x00E0)  # ty: ignore[invalid-argument-type]


def test_uds_doip_invalid_timeout_type():
    with pytest.raises(ValidationError):
        UdsDoip(
            ecu_ip="192.168.1.100",
            ecu_logical_address=0x00E0,
            request_timeout="not_a_float",  # ty: ignore[invalid-argument-type]
        )


@patch("jumpstarter_driver_uds_doip.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClientUDSConnector")
@patch("jumpstarter_driver_uds_doip.driver.DoIPClient")
def test_uds_doip_custom_config_forwarded(mock_doip_cls, mock_conn_cls, mock_uds_cls):
    """Verify non-default config values are passed to the underlying clients."""
    doip_mock, conn_mock, uds_mock = _make_mocks()
    mock_doip_cls.return_value = doip_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    UdsDoip(
        ecu_ip="10.0.0.1",
        ecu_logical_address=0x5678,
        tcp_port=9999,
        protocol_version=3,
        client_logical_address=0x0F00,
        auto_reconnect_tcp=True,
        request_timeout=10.0,
    )

    mock_doip_cls.assert_called_once_with(
        "10.0.0.1",
        0x5678,
        tcp_port=9999,
        protocol_version=3,
        client_logical_address=0x0F00,
        auto_reconnect_tcp=True,
    )
    mock_uds_cls.assert_called_once()
    call_args = mock_uds_cls.call_args
    assert call_args[0][0] is conn_mock
    assert call_args[1]["config"]["request_timeout"] == 10.0


# --- Integration tests with simulated DoIP + UDS server ---


def test_uds_doip_simulated_change_session(mock_doip_uds_server):
    driver = UdsDoip(
        ecu_ip="127.0.0.1",
        ecu_logical_address=0x00E0,
        tcp_port=mock_doip_uds_server,
        request_timeout=5.0,
    )
    with serve(driver) as client:
        resp = client.change_session(UdsSessionType.EXTENDED)
        assert resp.success is True
        assert resp.service == "DiagnosticSessionControl"


def test_uds_doip_simulated_ecu_reset(mock_doip_uds_server):
    driver = UdsDoip(
        ecu_ip="127.0.0.1",
        ecu_logical_address=0x00E0,
        tcp_port=mock_doip_uds_server,
        request_timeout=5.0,
    )
    with serve(driver) as client:
        resp = client.ecu_reset(UdsResetType.HARD)
        assert resp.success is True
        assert resp.service == "ECUReset"


def test_uds_doip_simulated_tester_present(mock_doip_uds_server):
    driver = UdsDoip(
        ecu_ip="127.0.0.1",
        ecu_logical_address=0x00E0,
        tcp_port=mock_doip_uds_server,
        request_timeout=5.0,
    )
    with serve(driver) as client:
        client.tester_present()


def test_uds_doip_simulated_request_seed(mock_doip_uds_server):
    driver = UdsDoip(
        ecu_ip="127.0.0.1",
        ecu_logical_address=0x00E0,
        tcp_port=mock_doip_uds_server,
        request_timeout=5.0,
    )
    with serve(driver) as client:
        seed_resp = client.request_seed(1)
        assert seed_resp.success is True
        assert seed_resp.seed == "deadbeef"


def test_uds_doip_simulated_send_key(mock_doip_uds_server):
    driver = UdsDoip(
        ecu_ip="127.0.0.1",
        ecu_logical_address=0x00E0,
        tcp_port=mock_doip_uds_server,
        request_timeout=5.0,
    )
    with serve(driver) as client:
        resp = client.send_key(1, b"\xAA\xBB\xCC\xDD")
        assert resp.success is True
        assert resp.service == "SecurityAccess"


def test_uds_doip_simulated_clear_dtc(mock_doip_uds_server):
    driver = UdsDoip(
        ecu_ip="127.0.0.1",
        ecu_logical_address=0x00E0,
        tcp_port=mock_doip_uds_server,
        request_timeout=5.0,
    )
    with serve(driver) as client:
        resp = client.clear_dtc()
        assert resp.success is True
        assert resp.service == "ClearDiagnosticInformation"


def test_uds_doip_simulated_read_dtc(mock_doip_uds_server):
    driver = UdsDoip(
        ecu_ip="127.0.0.1",
        ecu_logical_address=0x00E0,
        tcp_port=mock_doip_uds_server,
        request_timeout=5.0,
    )
    with serve(driver) as client:
        dtcs = client.read_dtc_by_status_mask(0xFF)
        assert len(dtcs) >= 1
        assert dtcs[0].status == 0x2F
