"""Tests for the OBD-II driver.

obd.OBD is patched with a mock ECU so the tests run without hardware,
exercising the @export RPC round-trip and query()'s value/error paths.
"""

from unittest.mock import MagicMock, patch

import obd
import pytest

from .driver import OBD, OBDConnectionStatus
from jumpstarter.client.core import DriverInvalidArgument
from jumpstarter.common.utils import serve


class _FakeStatus:
    """Stand-in for obd's Status: no __str__, plus a None key in __dict__
    (python-obd stores reserved test bits there) that broke serialize() on real hardware."""

    def __init__(self):
        self.MIL = False
        self.DTC_count = 0
        self.ignition_type = "spark"
        self.__dict__[None] = "reserved"  # the real-hardware crash trigger


def _make_mock_connection(status=obd.OBDStatus.CAR_CONNECTED):
    """Return a mock obd.OBD that looks like a connected car ECU."""
    conn = MagicMock()
    conn.status.return_value = status
    conn.is_connected.return_value = status == obd.OBDStatus.CAR_CONNECTED

    # obd.commands is populated dynamically; index by name
    rpm_cmd = obd.commands["RPM"]
    speed_cmd = obd.commands["SPEED"]
    vin_cmd = obd.commands["VIN"]
    status_cmd = obd.commands["STATUS"]

    conn.supported_commands = {rpm_cmd, speed_cmd, vin_cmd, status_cmd}

    def fake_query(cmd, *args, **kwargs):  # clear_dtc passes force=True
        # cover the value types query() must serialize: Quantity, bytearray, no-__str__ object
        resp = MagicMock()
        resp.is_null.return_value = False
        if cmd is rpm_cmd:
            resp.value = 3000 * obd.Unit.rpm
        elif cmd is speed_cmd:
            resp.value = 60 * obd.Unit.kph
        elif cmd is vin_cmd:
            resp.value = bytearray(b"1HGBH41JXMN109186")
        elif cmd is status_cmd:
            resp.value = _FakeStatus()
        else:
            resp.is_null.return_value = True
            resp.value = None
        return resp

    conn.query.side_effect = fake_query
    return conn


@pytest.fixture
def obd_client():
    """Yield an OBDClient connected to a mocked OBD driver."""
    mock_conn = _make_mock_connection()
    with patch("jumpstarter_driver_obd.driver.obd.OBD", return_value=mock_conn):
        with serve(OBD()) as client:
            yield client


def test_obd_status(obd_client):
    result = obd_client.status()
    assert result == OBDConnectionStatus.CAR_CONNECTED


def test_obd_supported_commands(obd_client):
    commands = obd_client.supported_commands()
    assert isinstance(commands, list)
    assert len(commands) > 0


def test_obd_query_rpm(obd_client):
    value = obd_client.query("RPM")
    assert value is not None
    assert float(value.split()[0]) == 3000


def test_obd_query_speed(obd_client):
    value = obd_client.query("SPEED")
    assert value is not None
    assert float(value.split()[0]) == 60


def test_obd_is_connected(obd_client):
    assert obd_client.is_connected() is True


def test_obd_query_unknown_command(obd_client):
    with pytest.raises(DriverInvalidArgument, match="Unknown OBD command"):
        obd_client.query("DOES_NOT_EXIST")


def test_obd_query_null_response(obd_client):
    # COOLANT_TEMP exists in obd.commands but the mock returns is_null=True for it
    value = obd_client.query("COOLANT_TEMP")
    assert value is None


def test_obd_query_bytearray_decoded(obd_client):
    # VIN comes back as a bytearray; query() must decode it, not str() the repr.
    value = obd_client.query("VIN")
    assert value == "1HGBH41JXMN109186"


def test_obd_query_object_without_str_serialized(obd_client):
    # STATUS has no __str__; query() must render fields, not a '<object at 0x...>' address
    value = obd_client.query("STATUS")
    assert "0x" not in value
    assert "object at" not in value
    assert "MIL=False" in value and "DTC_count=0" in value


def test_obd_query_rejects_destructive(obd_client):
    # CLEAR_DTC (mode 04) erases codes + resets monitors; query() must refuse it.
    with pytest.raises(DriverInvalidArgument, match="destructive"):
        obd_client.query("CLEAR_DTC")


def test_obd_clear_dtc_invokes_mode_04():
    # The dedicated method must actually send CLEAR_DTC to the adapter.
    mock_conn = _make_mock_connection()
    with patch("jumpstarter_driver_obd.driver.obd.OBD", return_value=mock_conn):
        with serve(OBD()) as client:
            assert client.clear_dtc() is None
    sent = [call.args[0] for call in mock_conn.query.call_args_list]
    assert obd.commands["CLEAR_DTC"] in sent


def test_obd_no_adapter_raises():
    """Driver must raise ConnectionError when no ELM327 adapter is found."""
    mock_conn = _make_mock_connection(status=obd.OBDStatus.NOT_CONNECTED)
    with patch("jumpstarter_driver_obd.driver.obd.OBD", return_value=mock_conn):
        with pytest.raises(ConnectionError, match="No ELM327 adapter found"):
            OBD()
