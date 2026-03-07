from unittest.mock import MagicMock, patch

import pytest
from jumpstarter_driver_can.common import IsoTpParams
from jumpstarter_driver_uds.common import UdsResetType, UdsSessionType
from pydantic import ValidationError
from udsoncan.exceptions import NegativeResponseException

from .driver import UdsCan
from jumpstarter.client.core import DriverError
from jumpstarter.common.utils import serve


def _make_mock_uds_response(positive=True, data=None):
    resp = MagicMock()
    resp.positive = positive
    resp.data = data
    return resp


def _make_mocks():
    bus_mock = MagicMock()
    notifier_mock = MagicMock()
    stack_mock = MagicMock()
    conn_mock = MagicMock()
    uds_mock = MagicMock()
    return bus_mock, notifier_mock, stack_mock, conn_mock, uds_mock


@patch("jumpstarter_driver_uds_can.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_can.driver.PythonIsoTpConnection")
@patch("jumpstarter_driver_uds_can.driver.isotp.NotifierBasedCanStack")
@patch("jumpstarter_driver_uds_can.driver.can.Notifier")
@patch("jumpstarter_driver_uds_can.driver.can.Bus")
def test_uds_can_change_session(mock_bus_cls, mock_notifier_cls, mock_stack_cls, mock_conn_cls, mock_uds_cls):
    bus_mock, notifier_mock, stack_mock, conn_mock, uds_mock = _make_mocks()
    mock_bus_cls.return_value = bus_mock
    mock_notifier_cls.return_value = notifier_mock
    mock_stack_cls.return_value = stack_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    uds_mock.change_session.return_value = _make_mock_uds_response()

    driver = UdsCan(channel="vcan0", rxid=0x641, txid=0x642)
    with serve(driver) as client:
        resp = client.change_session(UdsSessionType.EXTENDED)
        assert resp.success is True
        assert resp.service == "DiagnosticSessionControl"


@patch("jumpstarter_driver_uds_can.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_can.driver.PythonIsoTpConnection")
@patch("jumpstarter_driver_uds_can.driver.isotp.NotifierBasedCanStack")
@patch("jumpstarter_driver_uds_can.driver.can.Notifier")
@patch("jumpstarter_driver_uds_can.driver.can.Bus")
def test_uds_can_ecu_reset(mock_bus_cls, mock_notifier_cls, mock_stack_cls, mock_conn_cls, mock_uds_cls):
    bus_mock, notifier_mock, stack_mock, conn_mock, uds_mock = _make_mocks()
    mock_bus_cls.return_value = bus_mock
    mock_notifier_cls.return_value = notifier_mock
    mock_stack_cls.return_value = stack_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    uds_mock.ecu_reset.return_value = _make_mock_uds_response()

    driver = UdsCan(channel="vcan0", rxid=0x641, txid=0x642)
    with serve(driver) as client:
        resp = client.ecu_reset(UdsResetType.HARD)
        assert resp.success is True
        assert resp.service == "ECUReset"


@patch("jumpstarter_driver_uds_can.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_can.driver.PythonIsoTpConnection")
@patch("jumpstarter_driver_uds_can.driver.isotp.NotifierBasedCanStack")
@patch("jumpstarter_driver_uds_can.driver.can.Notifier")
@patch("jumpstarter_driver_uds_can.driver.can.Bus")
def test_uds_can_tester_present(mock_bus_cls, mock_notifier_cls, mock_stack_cls, mock_conn_cls, mock_uds_cls):
    bus_mock, notifier_mock, stack_mock, conn_mock, uds_mock = _make_mocks()
    mock_bus_cls.return_value = bus_mock
    mock_notifier_cls.return_value = notifier_mock
    mock_stack_cls.return_value = stack_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    driver = UdsCan(channel="vcan0", rxid=0x641, txid=0x642)
    with serve(driver) as client:
        client.tester_present()
        uds_mock.tester_present.assert_called_once()


@patch("jumpstarter_driver_uds_can.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_can.driver.PythonIsoTpConnection")
@patch("jumpstarter_driver_uds_can.driver.isotp.NotifierBasedCanStack")
@patch("jumpstarter_driver_uds_can.driver.can.Notifier")
@patch("jumpstarter_driver_uds_can.driver.can.Bus")
def test_uds_can_read_data_by_identifier(mock_bus_cls, mock_notifier_cls, mock_stack_cls, mock_conn_cls, mock_uds_cls):
    bus_mock, notifier_mock, stack_mock, conn_mock, uds_mock = _make_mocks()
    mock_bus_cls.return_value = bus_mock
    mock_notifier_cls.return_value = notifier_mock
    mock_stack_cls.return_value = stack_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    resp = _make_mock_uds_response()
    resp.service_data = MagicMock()
    resp.service_data.values = {0xF190: b"WDB1234567890ABCD"}
    uds_mock.read_data_by_identifier.return_value = resp

    driver = UdsCan(channel="vcan0", rxid=0x641, txid=0x642)
    with serve(driver) as client:
        values = client.read_data_by_identifier([0xF190])
        assert len(values) == 1
        assert values[0].did == 0xF190


@patch("jumpstarter_driver_uds_can.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_can.driver.PythonIsoTpConnection")
@patch("jumpstarter_driver_uds_can.driver.isotp.NotifierBasedCanStack")
@patch("jumpstarter_driver_uds_can.driver.can.Notifier")
@patch("jumpstarter_driver_uds_can.driver.can.Bus")
def test_uds_can_write_data_by_identifier(mock_bus_cls, mock_notifier_cls, mock_stack_cls, mock_conn_cls, mock_uds_cls):
    bus_mock, notifier_mock, stack_mock, conn_mock, uds_mock = _make_mocks()
    mock_bus_cls.return_value = bus_mock
    mock_notifier_cls.return_value = notifier_mock
    mock_stack_cls.return_value = stack_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    uds_mock.write_data_by_identifier.return_value = _make_mock_uds_response()

    driver = UdsCan(channel="vcan0", rxid=0x641, txid=0x642)
    with serve(driver) as client:
        resp = client.write_data_by_identifier(0xF190, b"ABC123456789")
        assert resp.success is True
        assert resp.service == "WriteDataByIdentifier"


@patch("jumpstarter_driver_uds_can.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_can.driver.PythonIsoTpConnection")
@patch("jumpstarter_driver_uds_can.driver.isotp.NotifierBasedCanStack")
@patch("jumpstarter_driver_uds_can.driver.can.Notifier")
@patch("jumpstarter_driver_uds_can.driver.can.Bus")
def test_uds_can_request_seed(mock_bus_cls, mock_notifier_cls, mock_stack_cls, mock_conn_cls, mock_uds_cls):
    bus_mock, notifier_mock, stack_mock, conn_mock, uds_mock = _make_mocks()
    mock_bus_cls.return_value = bus_mock
    mock_notifier_cls.return_value = notifier_mock
    mock_stack_cls.return_value = stack_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    resp = _make_mock_uds_response()
    resp.service_data = MagicMock()
    resp.service_data.seed = b"\x01\x02\x03\x04"
    uds_mock.request_seed.return_value = resp

    driver = UdsCan(channel="vcan0", rxid=0x641, txid=0x642)
    with serve(driver) as client:
        seed_resp = client.request_seed(1)
        assert seed_resp.seed == "01020304"


@patch("jumpstarter_driver_uds_can.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_can.driver.PythonIsoTpConnection")
@patch("jumpstarter_driver_uds_can.driver.isotp.NotifierBasedCanStack")
@patch("jumpstarter_driver_uds_can.driver.can.Notifier")
@patch("jumpstarter_driver_uds_can.driver.can.Bus")
def test_uds_can_send_key(mock_bus_cls, mock_notifier_cls, mock_stack_cls, mock_conn_cls, mock_uds_cls):
    bus_mock, notifier_mock, stack_mock, conn_mock, uds_mock = _make_mocks()
    mock_bus_cls.return_value = bus_mock
    mock_notifier_cls.return_value = notifier_mock
    mock_stack_cls.return_value = stack_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    uds_mock.send_key.return_value = _make_mock_uds_response()

    driver = UdsCan(channel="vcan0", rxid=0x641, txid=0x642)
    with serve(driver) as client:
        resp = client.send_key(1, b"\xAA\xBB\xCC\xDD")
        assert resp.success is True
        assert resp.service == "SecurityAccess"


@patch("jumpstarter_driver_uds_can.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_can.driver.PythonIsoTpConnection")
@patch("jumpstarter_driver_uds_can.driver.isotp.NotifierBasedCanStack")
@patch("jumpstarter_driver_uds_can.driver.can.Notifier")
@patch("jumpstarter_driver_uds_can.driver.can.Bus")
def test_uds_can_clear_dtc(mock_bus_cls, mock_notifier_cls, mock_stack_cls, mock_conn_cls, mock_uds_cls):
    bus_mock, notifier_mock, stack_mock, conn_mock, uds_mock = _make_mocks()
    mock_bus_cls.return_value = bus_mock
    mock_notifier_cls.return_value = notifier_mock
    mock_stack_cls.return_value = stack_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    uds_mock.clear_dtc.return_value = _make_mock_uds_response()

    driver = UdsCan(channel="vcan0", rxid=0x641, txid=0x642)
    with serve(driver) as client:
        resp = client.clear_dtc()
        assert resp.success is True
        assert resp.service == "ClearDiagnosticInformation"


@patch("jumpstarter_driver_uds_can.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_can.driver.PythonIsoTpConnection")
@patch("jumpstarter_driver_uds_can.driver.isotp.NotifierBasedCanStack")
@patch("jumpstarter_driver_uds_can.driver.can.Notifier")
@patch("jumpstarter_driver_uds_can.driver.can.Bus")
def test_uds_can_read_dtc_by_status_mask(mock_bus_cls, mock_notifier_cls, mock_stack_cls, mock_conn_cls, mock_uds_cls):
    bus_mock, notifier_mock, stack_mock, conn_mock, uds_mock = _make_mocks()
    mock_bus_cls.return_value = bus_mock
    mock_notifier_cls.return_value = notifier_mock
    mock_stack_cls.return_value = stack_mock
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

    driver = UdsCan(channel="vcan0", rxid=0x641, txid=0x642)
    with serve(driver) as client:
        dtcs = client.read_dtc_by_status_mask(0xFF)
        assert len(dtcs) == 1
        assert dtcs[0].dtc_id == 0x123456
        assert dtcs[0].status == 0x2F


# --- Error path tests ---


@patch("jumpstarter_driver_uds_can.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_can.driver.PythonIsoTpConnection")
@patch("jumpstarter_driver_uds_can.driver.isotp.NotifierBasedCanStack")
@patch("jumpstarter_driver_uds_can.driver.can.Notifier")
@patch("jumpstarter_driver_uds_can.driver.can.Bus")
def test_uds_can_bus_failure(mock_bus_cls, _mock_notifier, _mock_stack, _mock_conn, _mock_uds):
    mock_bus_cls.side_effect = OSError("No such device: vcan0")
    with pytest.raises(OSError, match="No such device"):
        UdsCan(channel="vcan0", rxid=0x641, txid=0x642)


@patch("jumpstarter_driver_uds_can.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_can.driver.PythonIsoTpConnection")
@patch("jumpstarter_driver_uds_can.driver.isotp.NotifierBasedCanStack")
@patch("jumpstarter_driver_uds_can.driver.can.Notifier")
@patch("jumpstarter_driver_uds_can.driver.can.Bus")
def test_uds_can_timeout_on_read(mock_bus_cls, mock_notifier_cls, mock_stack_cls, mock_conn_cls, mock_uds_cls):
    bus_mock, notifier_mock, stack_mock, conn_mock, uds_mock = _make_mocks()
    mock_bus_cls.return_value = bus_mock
    mock_notifier_cls.return_value = notifier_mock
    mock_stack_cls.return_value = stack_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    uds_mock.read_data_by_identifier.side_effect = TimeoutError("No CAN response")

    driver = UdsCan(channel="vcan0", rxid=0x641, txid=0x642)
    with serve(driver) as client:
        with pytest.raises(DriverError, match="No CAN response"):
            client.read_data_by_identifier([0xF190])


@patch("jumpstarter_driver_uds_can.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_can.driver.PythonIsoTpConnection")
@patch("jumpstarter_driver_uds_can.driver.isotp.NotifierBasedCanStack")
@patch("jumpstarter_driver_uds_can.driver.can.Notifier")
@patch("jumpstarter_driver_uds_can.driver.can.Bus")
def test_uds_can_nrc_on_write(mock_bus_cls, mock_notifier_cls, mock_stack_cls, mock_conn_cls, mock_uds_cls):
    bus_mock, notifier_mock, stack_mock, conn_mock, uds_mock = _make_mocks()
    mock_bus_cls.return_value = bus_mock
    mock_notifier_cls.return_value = notifier_mock
    mock_stack_cls.return_value = stack_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    nrc_response = MagicMock()
    nrc_response.code = 0x72
    nrc_response.code_name = "generalProgrammingFailure"
    nrc_response.valid = True
    nrc_response.service = MagicMock()
    nrc_response.service.response_id.return_value = 0x6E
    uds_mock.write_data_by_identifier.side_effect = NegativeResponseException(nrc_response)

    driver = UdsCan(channel="vcan0", rxid=0x641, txid=0x642)
    with serve(driver) as client:
        resp = client.write_data_by_identifier(0xF190, b"BADDATA")
        assert resp.success is False
        assert resp.nrc == 0x72
        assert resp.nrc_name == "generalProgrammingFailure"


@patch("jumpstarter_driver_uds_can.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_can.driver.PythonIsoTpConnection")
@patch("jumpstarter_driver_uds_can.driver.isotp.NotifierBasedCanStack")
@patch("jumpstarter_driver_uds_can.driver.can.Notifier")
@patch("jumpstarter_driver_uds_can.driver.can.Bus")
def test_uds_can_close_resources(mock_bus_cls, mock_notifier_cls, mock_stack_cls, mock_conn_cls, mock_uds_cls):
    """Verify close() shuts down both UDS client and CAN bus."""
    bus_mock, notifier_mock, stack_mock, conn_mock, uds_mock = _make_mocks()
    mock_bus_cls.return_value = bus_mock
    mock_notifier_cls.return_value = notifier_mock
    mock_stack_cls.return_value = stack_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    driver = UdsCan(channel="vcan0", rxid=0x641, txid=0x642)
    driver.close()
    uds_mock.close.assert_called_once()
    bus_mock.shutdown.assert_called_once()


# --- Config validation tests ---


def test_uds_can_missing_required_channel():
    with pytest.raises(ValidationError, match="channel"):
        UdsCan(rxid=0x641, txid=0x642)


def test_uds_can_missing_required_rxid():
    with pytest.raises(ValidationError, match="rxid"):
        UdsCan(channel="vcan0", txid=0x642)


def test_uds_can_missing_required_txid():
    with pytest.raises(ValidationError, match="txid"):
        UdsCan(channel="vcan0", rxid=0x641)


def test_uds_can_invalid_channel_type():
    with pytest.raises(ValidationError):
        UdsCan(channel=12345, rxid=0x641, txid=0x642)


def test_uds_can_invalid_timeout_type():
    with pytest.raises(ValidationError):
        UdsCan(channel="vcan0", rxid=0x641, txid=0x642, request_timeout="not_a_float")


@patch("jumpstarter_driver_uds_can.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_can.driver.PythonIsoTpConnection")
@patch("jumpstarter_driver_uds_can.driver.isotp.NotifierBasedCanStack")
@patch("jumpstarter_driver_uds_can.driver.can.Notifier")
@patch("jumpstarter_driver_uds_can.driver.can.Bus")
def test_uds_can_custom_isotp_params(mock_bus_cls, mock_notifier_cls, mock_stack_cls, mock_conn_cls, mock_uds_cls):
    """Verify custom IsoTpParams are forwarded to the ISO-TP stack."""
    bus_mock, notifier_mock, stack_mock, conn_mock, uds_mock = _make_mocks()
    mock_bus_cls.return_value = bus_mock
    mock_notifier_cls.return_value = notifier_mock
    mock_stack_cls.return_value = stack_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    custom_params = IsoTpParams(stmin=32, blocksize=4, tx_data_length=16)
    UdsCan(
        channel="vcan0",
        interface="virtual",
        rxid=0x641,
        txid=0x642,
        isotp_params=custom_params,
    )

    stack_call_kwargs = mock_stack_cls.call_args
    passed_params = stack_call_kwargs.kwargs.get("params") or stack_call_kwargs[1].get("params")
    assert passed_params["stmin"] == 32
    assert passed_params["blocksize"] == 4
    assert passed_params["tx_data_length"] == 16


@patch("jumpstarter_driver_uds_can.driver.UdsoncanClient")
@patch("jumpstarter_driver_uds_can.driver.PythonIsoTpConnection")
@patch("jumpstarter_driver_uds_can.driver.isotp.NotifierBasedCanStack")
@patch("jumpstarter_driver_uds_can.driver.can.Notifier")
@patch("jumpstarter_driver_uds_can.driver.can.Bus")
def test_uds_can_custom_config_forwarded(mock_bus_cls, mock_notifier_cls, mock_stack_cls, mock_conn_cls, mock_uds_cls):
    """Verify non-default config values are passed to the underlying clients."""
    bus_mock, notifier_mock, stack_mock, conn_mock, uds_mock = _make_mocks()
    mock_bus_cls.return_value = bus_mock
    mock_notifier_cls.return_value = notifier_mock
    mock_stack_cls.return_value = stack_mock
    mock_conn_cls.return_value = conn_mock
    mock_uds_cls.return_value = uds_mock

    UdsCan(
        channel="can1",
        interface="pcan",
        rxid=0x700,
        txid=0x701,
        request_timeout=15.0,
    )

    mock_bus_cls.assert_called_once_with(channel="can1", interface="pcan")
    mock_uds_cls.assert_called_once_with(conn_mock, request_timeout=15.0)


# --- Integration tests with virtual CAN bus + MockUdsEcu ---


def test_uds_can_virtual_change_session(mock_uds_ecu):
    channel, rxid, txid = mock_uds_ecu
    driver = UdsCan(channel=channel, interface="virtual", rxid=rxid, txid=txid, request_timeout=5.0)
    with serve(driver) as client:
        resp = client.change_session(UdsSessionType.EXTENDED)
        assert resp.success is True
        assert resp.service == "DiagnosticSessionControl"


def test_uds_can_virtual_ecu_reset(mock_uds_ecu):
    channel, rxid, txid = mock_uds_ecu
    driver = UdsCan(channel=channel, interface="virtual", rxid=rxid, txid=txid, request_timeout=5.0)
    with serve(driver) as client:
        resp = client.ecu_reset(UdsResetType.HARD)
        assert resp.success is True
        assert resp.service == "ECUReset"


def test_uds_can_virtual_tester_present(mock_uds_ecu):
    channel, rxid, txid = mock_uds_ecu
    driver = UdsCan(channel=channel, interface="virtual", rxid=rxid, txid=txid, request_timeout=5.0)
    with serve(driver) as client:
        client.tester_present()


def test_uds_can_virtual_request_seed(mock_uds_ecu):
    channel, rxid, txid = mock_uds_ecu
    driver = UdsCan(channel=channel, interface="virtual", rxid=rxid, txid=txid, request_timeout=5.0)
    with serve(driver) as client:
        seed_resp = client.request_seed(1)
        assert seed_resp.success is True
        assert seed_resp.seed == "deadbeef"


def test_uds_can_virtual_send_key(mock_uds_ecu):
    channel, rxid, txid = mock_uds_ecu
    driver = UdsCan(channel=channel, interface="virtual", rxid=rxid, txid=txid, request_timeout=5.0)
    with serve(driver) as client:
        resp = client.send_key(1, b"\xAA\xBB\xCC\xDD")
        assert resp.success is True
        assert resp.service == "SecurityAccess"


def test_uds_can_virtual_clear_dtc(mock_uds_ecu):
    channel, rxid, txid = mock_uds_ecu
    driver = UdsCan(channel=channel, interface="virtual", rxid=rxid, txid=txid, request_timeout=5.0)
    with serve(driver) as client:
        resp = client.clear_dtc()
        assert resp.success is True
        assert resp.service == "ClearDiagnosticInformation"


def test_uds_can_virtual_read_dtc(mock_uds_ecu):
    channel, rxid, txid = mock_uds_ecu
    driver = UdsCan(channel=channel, interface="virtual", rxid=rxid, txid=txid, request_timeout=5.0)
    with serve(driver) as client:
        dtcs = client.read_dtc_by_status_mask(0xFF)
        assert len(dtcs) >= 1
        assert dtcs[0].status == 0x2F
