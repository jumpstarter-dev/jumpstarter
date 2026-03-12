"""Tests for the shared UdsInterface base class.

These tests verify that UdsInterface correctly delegates to _uds_client
by using a mock UDS driver that inherits from UdsInterface.
"""

from unittest.mock import MagicMock

from pydantic.dataclasses import dataclass

from .common import UdsResetType, UdsSessionType
from .driver import UdsInterface
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver


@dataclass(kw_only=True)
class MockUdsDriver(UdsInterface, Driver):
    """Minimal UDS driver backed by a mock udsoncan client."""

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        self._uds_client = MagicMock()


def _make_mock_uds_response(positive=True, data=None):
    resp = MagicMock()
    resp.positive = positive
    resp.data = data
    return resp


def test_uds_change_session():
    driver = MockUdsDriver()
    driver._uds_client.change_session.return_value = _make_mock_uds_response()

    with serve(driver) as client:
        resp = client.change_session(UdsSessionType.EXTENDED)
        assert resp.success is True
        assert resp.service == "DiagnosticSessionControl"


def test_uds_ecu_reset():
    driver = MockUdsDriver()
    driver._uds_client.ecu_reset.return_value = _make_mock_uds_response()

    with serve(driver) as client:
        resp = client.ecu_reset(UdsResetType.HARD)
        assert resp.success is True
        assert resp.service == "ECUReset"


def test_uds_tester_present():
    driver = MockUdsDriver()

    with serve(driver) as client:
        client.tester_present()
        driver._uds_client.tester_present.assert_called_once()


def test_uds_read_data_by_identifier():
    driver = MockUdsDriver()
    resp = _make_mock_uds_response()
    resp.service_data = MagicMock()
    resp.service_data.values = {0xF190: b"WDB1234567890ABCD"}
    driver._uds_client.read_data_by_identifier.return_value = resp

    with serve(driver) as client:
        values = client.read_data_by_identifier([0xF190])
        assert len(values) == 1
        assert values[0].did == 0xF190


def test_uds_write_data_by_identifier():
    driver = MockUdsDriver()
    driver._uds_client.write_data_by_identifier.return_value = _make_mock_uds_response()

    with serve(driver) as client:
        resp = client.write_data_by_identifier(0xF190, b"ABC123456789")
        assert resp.success is True
        assert resp.service == "WriteDataByIdentifier"


def test_uds_request_seed():
    driver = MockUdsDriver()
    resp = _make_mock_uds_response()
    resp.service_data = MagicMock()
    resp.service_data.seed = b"\x01\x02\x03\x04"
    driver._uds_client.request_seed.return_value = resp

    with serve(driver) as client:
        seed_resp = client.request_seed(1)
        assert seed_resp.seed == "01020304"
        assert seed_resp.success is True


def test_uds_request_seed_negative_response():
    from udsoncan.exceptions import NegativeResponseException

    driver = MockUdsDriver()
    nrc_response = MagicMock()
    nrc_response.code = 0x35
    nrc_response.code_name = "invalidKey"
    driver._uds_client.request_seed.side_effect = NegativeResponseException(nrc_response)

    with serve(driver) as client:
        seed_resp = client.request_seed(1)
        assert seed_resp.success is False
        assert seed_resp.seed == ""
        assert seed_resp.nrc == 0x35
        assert seed_resp.nrc_name == "invalidKey"


def test_uds_send_key():
    driver = MockUdsDriver()
    driver._uds_client.send_key.return_value = _make_mock_uds_response()

    with serve(driver) as client:
        resp = client.send_key(1, b"\xAA\xBB\xCC\xDD")
        assert resp.success is True
        assert resp.service == "SecurityAccess"


def test_uds_clear_dtc():
    driver = MockUdsDriver()
    driver._uds_client.clear_dtc.return_value = _make_mock_uds_response()

    with serve(driver) as client:
        resp = client.clear_dtc()
        assert resp.success is True
        assert resp.service == "ClearDiagnosticInformation"


def test_uds_read_dtc_by_status_mask():
    driver = MockUdsDriver()
    dtc1 = MagicMock()
    dtc1.id = 0x123456
    dtc1.status = MagicMock()
    dtc1.status.get_byte_as_int.return_value = 0x2F
    dtc1.severity = None

    resp = _make_mock_uds_response()
    resp.service_data = MagicMock()
    resp.service_data.dtcs = [dtc1]
    driver._uds_client.get_dtc_by_status_mask.return_value = resp

    with serve(driver) as client:
        dtcs = client.read_dtc_by_status_mask(0xFF)
        assert len(dtcs) == 1
        assert dtcs[0].dtc_id == 0x123456
        assert dtcs[0].status == 0x2F
