from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from .driver import DoIP
from jumpstarter.client.core import DriverError
from jumpstarter.common.utils import serve


def _make_mock_doip_client():
    mock = MagicMock()

    entity_resp = MagicMock()
    entity_resp.node_type = 0
    entity_resp.max_open_sockets = 16
    entity_resp.currently_open_sockets = 1
    entity_resp.max_data_size = 4096
    mock.request_entity_status.return_value = entity_resp

    alive_resp = MagicMock()
    alive_resp.source_address = 0x00E0
    mock.request_alive_check.return_value = alive_resp

    power_resp = MagicMock()
    power_resp.diagnostic_power_mode = 1
    mock.request_diagnostic_power_mode.return_value = power_resp

    vehicle_resp = MagicMock()
    vehicle_resp.vin = b"WDB1234567890ABCD"
    vehicle_resp.logical_address = 0x00E0
    vehicle_resp.eid = b"\x00\x01\x02\x03\x04\x05"
    vehicle_resp.gid = b"\x00\x01\x02\x03\x04\x05"
    vehicle_resp.further_action = 0
    vehicle_resp.sync_status = 0
    mock.request_vehicle_identification.return_value = vehicle_resp

    activation_resp = MagicMock()
    activation_resp.client_logical_address = 0x0E00
    activation_resp.logical_address = 0x00E0
    activation_resp.response_code = 0x10
    activation_resp.vm_specific = None
    mock.request_activation.return_value = activation_resp

    mock.receive_diagnostic.return_value = bytearray(b"\x62\xf1\x90")

    return mock


@patch("jumpstarter_driver_doip.driver.DoIPClient")
def test_doip_entity_status(mock_doip_cls):
    mock_client = _make_mock_doip_client()
    mock_doip_cls.return_value = mock_client

    driver = DoIP(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        resp = client.entity_status()
        assert resp.node_type == 0
        assert resp.max_open_sockets == 16
        assert resp.currently_open_sockets == 1
        assert resp.max_data_size == 4096


@patch("jumpstarter_driver_doip.driver.DoIPClient")
def test_doip_alive_check(mock_doip_cls):
    mock_client = _make_mock_doip_client()
    mock_doip_cls.return_value = mock_client

    driver = DoIP(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        resp = client.alive_check()
        assert resp.source_address == 0x00E0


@patch("jumpstarter_driver_doip.driver.DoIPClient")
def test_doip_diagnostic_power_mode(mock_doip_cls):
    mock_client = _make_mock_doip_client()
    mock_doip_cls.return_value = mock_client

    driver = DoIP(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        resp = client.diagnostic_power_mode()
        assert resp.diagnostic_power_mode == 1


@patch("jumpstarter_driver_doip.driver.DoIPClient")
def test_doip_vehicle_identification(mock_doip_cls):
    mock_client = _make_mock_doip_client()
    mock_doip_cls.return_value = mock_client

    driver = DoIP(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        resp = client.request_vehicle_identification()
        assert resp.vin == "WDB1234567890ABCD"
        assert resp.logical_address == 0x00E0
        assert resp.further_action == 0


@patch("jumpstarter_driver_doip.driver.DoIPClient")
def test_doip_vehicle_identification_with_vin(mock_doip_cls):
    mock_client = _make_mock_doip_client()
    mock_doip_cls.return_value = mock_client

    driver = DoIP(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        resp = client.request_vehicle_identification(vin="WDB1234567890ABCD")
        assert resp.vin == "WDB1234567890ABCD"
        mock_client.request_vehicle_identification.assert_called_with(vin="WDB1234567890ABCD")


@patch("jumpstarter_driver_doip.driver.DoIPClient")
def test_doip_routing_activation(mock_doip_cls):
    mock_client = _make_mock_doip_client()
    mock_doip_cls.return_value = mock_client

    driver = DoIP(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        resp = client.routing_activation(activation_type=0)
        assert resp.client_logical_address == 0x0E00
        assert resp.logical_address == 0x00E0
        assert resp.response_code == 0x10


@patch("jumpstarter_driver_doip.driver.DoIPClient")
def test_doip_send_receive_diagnostic(mock_doip_cls):
    mock_client = _make_mock_doip_client()
    mock_doip_cls.return_value = mock_client

    driver = DoIP(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        client.send_diagnostic(b"\x22\xf1\x90")
        mock_client.send_diagnostic.assert_called_once_with(b"\x22\xf1\x90")

        resp = client.receive_diagnostic(timeout=2.0)
        assert resp == b"\x62\xf1\x90"


@patch("jumpstarter_driver_doip.driver.DoIPClient")
def test_doip_reconnect(mock_doip_cls):
    mock_client = _make_mock_doip_client()
    mock_doip_cls.return_value = mock_client

    driver = DoIP(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        client.reconnect(close_delay=1.0)
        mock_client.reconnect.assert_called_once_with(close_delay=1.0)


@patch("jumpstarter_driver_doip.driver.DoIPClient")
def test_doip_close_connection(mock_doip_cls):
    mock_client = _make_mock_doip_client()
    mock_doip_cls.return_value = mock_client

    driver = DoIP(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        client.close_connection()
        mock_client.close.assert_called_once()


# --- Error path tests ---


@patch("jumpstarter_driver_doip.driver.DoIPClient")
def test_doip_connection_failure(mock_doip_cls):
    mock_doip_cls.side_effect = ConnectionRefusedError("Connection refused")
    with pytest.raises(ConnectionRefusedError, match="Connection refused"):
        DoIP(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)


@patch("jumpstarter_driver_doip.driver.DoIPClient")
def test_doip_receive_diagnostic_timeout(mock_doip_cls):
    mock_client = _make_mock_doip_client()
    mock_client.receive_diagnostic.side_effect = TimeoutError("No response from ECU")
    mock_doip_cls.return_value = mock_client

    driver = DoIP(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        with pytest.raises(DriverError, match="No response from ECU"):
            client.receive_diagnostic(timeout=0.1)


@patch("jumpstarter_driver_doip.driver.DoIPClient")
def test_doip_entity_status_connection_error(mock_doip_cls):
    mock_client = _make_mock_doip_client()
    mock_client.request_entity_status.side_effect = ConnectionError("Lost connection")
    mock_doip_cls.return_value = mock_client

    driver = DoIP(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        with pytest.raises(DriverError, match="Lost connection"):
            client.entity_status()


@patch("jumpstarter_driver_doip.driver.DoIPClient")
def test_doip_reconnect_failure(mock_doip_cls):
    mock_client = _make_mock_doip_client()
    mock_client.reconnect.side_effect = OSError("Cannot reconnect")
    mock_doip_cls.return_value = mock_client

    driver = DoIP(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0)
    with serve(driver) as client:
        with pytest.raises(DriverError, match="Cannot reconnect"):
            client.reconnect(close_delay=0.1)


# --- Config validation tests ---


def test_doip_missing_required_ecu_ip():
    with pytest.raises(ValidationError, match="ecu_ip"):
        DoIP(ecu_logical_address=0x00E0)


def test_doip_missing_required_ecu_logical_address():
    with pytest.raises(ValidationError, match="ecu_logical_address"):
        DoIP(ecu_ip="192.168.1.100")


def test_doip_invalid_ecu_ip_type():
    with pytest.raises(ValidationError):
        DoIP(ecu_ip=12345, ecu_logical_address=0x00E0)


def test_doip_invalid_tcp_port_type():
    with pytest.raises(ValidationError):
        DoIP(ecu_ip="192.168.1.100", ecu_logical_address=0x00E0, tcp_port="not_a_port")


def test_doip_invalid_request_timeout_type():
    with pytest.raises(ValidationError):
        DoIP(
            ecu_ip="192.168.1.100",
            ecu_logical_address=0x00E0,
            auto_reconnect_tcp="not_bool",
        )


@patch("jumpstarter_driver_doip.driver.DoIPClient")
def test_doip_custom_config_forwarded(mock_doip_cls):
    """Verify non-default config values are passed to DoIPClient."""
    mock_doip_cls.return_value = _make_mock_doip_client()

    DoIP(
        ecu_ip="10.0.0.1",
        ecu_logical_address=0x1234,
        tcp_port=9999,
        protocol_version=3,
        client_logical_address=0x0F00,
        auto_reconnect_tcp=True,
        activation_type=1,
    )

    mock_doip_cls.assert_called_once_with(
        "10.0.0.1",
        0x1234,
        tcp_port=9999,
        protocol_version=3,
        client_logical_address=0x0F00,
        auto_reconnect_tcp=True,
        activation_type=1,
    )


# --- Integration tests with simulated DoIP server ---


def test_doip_simulated_alive_check(mock_doip_server):
    driver = DoIP(
        ecu_ip="127.0.0.1",
        ecu_logical_address=0x00E0,
        tcp_port=mock_doip_server,
        activation_type=None,
    )
    with serve(driver) as client:
        resp = client.alive_check()
        assert resp.source_address == 0x00E0


def test_doip_simulated_routing_activation(mock_doip_server):
    driver = DoIP(
        ecu_ip="127.0.0.1",
        ecu_logical_address=0x00E0,
        tcp_port=mock_doip_server,
        activation_type=None,
    )
    with serve(driver) as client:
        resp = client.routing_activation(activation_type=0)
        assert resp.client_logical_address == 0x0E00
        assert resp.logical_address == 0x00E0
        assert resp.response_code == 0x10


def test_doip_simulated_send_receive_diagnostic(mock_doip_server):
    driver = DoIP(
        ecu_ip="127.0.0.1",
        ecu_logical_address=0x00E0,
        tcp_port=mock_doip_server,
        activation_type=None,
    )
    with serve(driver) as client:
        client.send_diagnostic(b"\x22\xF1\x90")
        resp = client.receive_diagnostic(timeout=2.0)
        assert resp == b"\x22\xF1\x90"
