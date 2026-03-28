import os
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from .driver import SomeIp
from jumpstarter.client.core import DriverError
from jumpstarter.common.utils import serve


def _make_mock_message():
    mock_response = MagicMock()
    mock_response.message_id.service_id = 0x1234
    mock_response.message_id.method_id = 0x0001
    mock_response.request_id.client_id = 0x0001
    mock_response.request_id.session_id = 0x0001
    mock_response.protocol_version = 1
    mock_response.interface_version = 1
    mock_response.message_type = 0x80
    mock_response.return_code = 0x00
    mock_response.payload = b"\x01\x02\x03"
    return mock_response


def _make_mock_osip_client():
    import queue as _queue

    mock = MagicMock()

    mock_response = _make_mock_message()
    mock.call.return_value = mock_response

    sync_queue = _queue.Queue()
    sync_queue.put(mock_response)
    mock.transport.receiver._sync_queue = sync_queue

    event_queue = _queue.Queue()
    event_queue.put(mock_response)
    mock_event_receiver = MagicMock()
    mock_event_receiver._sync_queue = event_queue
    mock.event_subscriber.notifications.return_value = mock_event_receiver

    return mock


@patch("jumpstarter_driver_someip.driver.OsipClient")
def test_someip_rpc_call(mock_osip_cls):
    mock_client = _make_mock_osip_client()
    mock_osip_cls.return_value = mock_client

    driver = SomeIp(host="127.0.0.1", port=30490)
    with serve(driver) as client:
        resp = client.rpc_call(0x1234, 0x0001, b"\x01\x02\x03")
        assert resp.service_id == 0x1234
        assert resp.method_id == 0x0001
        assert resp.payload == "010203"
        assert resp.return_code == 0x00


@patch("jumpstarter_driver_someip.driver.OsipClient")
def test_someip_send_message(mock_osip_cls):
    mock_client = _make_mock_osip_client()
    mock_osip_cls.return_value = mock_client

    driver = SomeIp(host="127.0.0.1", port=30490)
    with serve(driver) as client:
        client.send_message(0x1234, 0x0001, b"\xAA\xBB")
        mock_client.send.assert_called_once()
        sent_msg = mock_client.send.call_args[0][0]
        assert sent_msg.message_id.service_id == 0x1234
        assert sent_msg.message_id.method_id == 0x0001
        assert sent_msg.payload == b"\xAA\xBB"


@patch("jumpstarter_driver_someip.driver.OsipClient")
def test_someip_receive_message(mock_osip_cls):
    mock_client = _make_mock_osip_client()
    mock_osip_cls.return_value = mock_client

    driver = SomeIp(host="127.0.0.1", port=30490)
    with serve(driver) as client:
        resp = client.receive_message(timeout=1.0)
        assert resp.service_id == 0x1234
        assert resp.payload == "010203"


@patch("jumpstarter_driver_someip.driver.OsipClient")
def test_someip_subscribe_eventgroup(mock_osip_cls):
    mock_client = _make_mock_osip_client()
    mock_osip_cls.return_value = mock_client

    driver = SomeIp(host="127.0.0.1", port=30490)
    with serve(driver) as client:
        client.subscribe_eventgroup(0x1234, 1)
        mock_client.subscribe_events.assert_called_once_with(1)


@patch("jumpstarter_driver_someip.driver.OsipClient")
def test_someip_unsubscribe_eventgroup(mock_osip_cls):
    mock_client = _make_mock_osip_client()
    mock_osip_cls.return_value = mock_client

    driver = SomeIp(host="127.0.0.1", port=30490)
    with serve(driver) as client:
        client.unsubscribe_eventgroup(0x1234, 1)
        mock_client.unsubscribe_events.assert_called_once_with(1)


@patch("jumpstarter_driver_someip.driver.OsipClient")
def test_someip_receive_event(mock_osip_cls):
    mock_client = _make_mock_osip_client()
    mock_osip_cls.return_value = mock_client

    driver = SomeIp(host="127.0.0.1", port=30490)
    with serve(driver) as client:
        resp = client.receive_event(timeout=1.0)
        assert resp.service_id == 0x1234
        assert resp.event_id == 0x0001
        assert resp.payload == "010203"


@patch("jumpstarter_driver_someip.driver.OsipClient")
def test_someip_close_connection(mock_osip_cls):
    mock_client = _make_mock_osip_client()
    mock_osip_cls.return_value = mock_client

    driver = SomeIp(host="127.0.0.1", port=30490)
    with serve(driver) as client:
        client.close_connection()
        mock_client.stop.assert_called()


@patch("jumpstarter_driver_someip.driver.OsipClient")
def test_someip_reconnect(mock_osip_cls):
    mock_client = _make_mock_osip_client()
    mock_osip_cls.return_value = mock_client

    driver = SomeIp(host="127.0.0.1", port=30490)
    with serve(driver) as client:
        client.reconnect()
        assert mock_client.stop.call_count >= 1
        assert mock_client.start.call_count >= 1


# --- Error path tests ---


@patch("jumpstarter_driver_someip.driver.OsipClient")
def test_someip_rpc_call_timeout(mock_osip_cls):
    mock_client = _make_mock_osip_client()
    mock_client.call.side_effect = TimeoutError("No response from service")
    mock_osip_cls.return_value = mock_client

    driver = SomeIp(host="127.0.0.1", port=30490)
    with serve(driver) as client:
        with pytest.raises(DriverError, match="No response from service"):
            client.rpc_call(0x1234, 0x0001, b"\x01")


@patch("jumpstarter_driver_someip.driver.OsipClient")
def test_someip_receive_message_timeout(mock_osip_cls):
    import queue as _queue

    mock_client = _make_mock_osip_client()
    mock_client.transport.receiver._sync_queue = _queue.Queue()
    mock_osip_cls.return_value = mock_client

    driver = SomeIp(host="127.0.0.1", port=30490)
    with serve(driver) as client:
        with pytest.raises(DriverError, match="No message received"):
            client.receive_message(timeout=0.1)


@patch("jumpstarter_driver_someip.driver.OsipClient")
def test_someip_connection_error(mock_osip_cls):
    mock_osip_cls.return_value.start.side_effect = ConnectionRefusedError("Connection refused")

    with pytest.raises(ConnectionRefusedError, match="Connection refused"):
        SomeIp(host="192.168.1.100", port=30490)


# --- Config validation tests ---


def test_someip_missing_required_host():
    with pytest.raises(ValidationError, match="host"):
        SomeIp(port=30490)


def test_someip_invalid_port_type():
    with pytest.raises(ValidationError):
        SomeIp(host="127.0.0.1", port="not_a_port")


def test_someip_invalid_transport_mode():
    with pytest.raises(ValidationError):
        SomeIp(host="127.0.0.1", transport_mode=12345)


@patch("jumpstarter_driver_someip.driver.OsipClient")
def test_someip_custom_config_forwarded(mock_osip_cls):
    """Verify non-default config values are passed to opensomeip."""
    mock_osip_cls.return_value = _make_mock_osip_client()

    SomeIp(
        host="10.0.0.1",
        port=9999,
        transport_mode="TCP",
        multicast_group="239.1.1.1",
        multicast_port=31000,
    )

    mock_osip_cls.assert_called_once()
    config = mock_osip_cls.call_args[0][0]
    assert config.local_endpoint.ip == "10.0.0.1"
    assert config.local_endpoint.port == 9999
    assert config.sd_config.multicast_endpoint.ip == "239.1.1.1"
    assert config.sd_config.multicast_endpoint.port == 31000


@patch("jumpstarter_driver_someip.driver.OsipClient")
def test_someip_tcp_transport_mode(mock_osip_cls):
    """Verify TCP transport mode is forwarded correctly."""
    mock_osip_cls.return_value = _make_mock_osip_client()

    SomeIp(host="127.0.0.1", transport_mode="TCP")

    config = mock_osip_cls.call_args[0][0]
    from opensomeip import TransportMode
    assert config.transport_mode == TransportMode.TCP


# --- Integration tests with simulated SOME/IP server ---
# opensomeip uses Service Discovery to locate services, so connecting to a
# raw TCP mock server requires a full SD-capable SOME/IP environment.
# These tests are intended for CI environments with proper SOME/IP networking
# and are skipped by default.  Set SOMEIP_INTEGRATION_TESTS=1 to enable.

_RUN_INTEGRATION = os.environ.get("SOMEIP_INTEGRATION_TESTS", "0") == "1"


@pytest.mark.skipif(not _RUN_INTEGRATION, reason="SOMEIP_INTEGRATION_TESTS not set")
def test_someip_simulated_rpc_call(mock_someip_server):
    driver = SomeIp(
        host="127.0.0.1",
        port=mock_someip_server,
        transport_mode="TCP",
    )
    with serve(driver) as client:
        resp = client.rpc_call(0x1234, 0x0001, b"\x01\x02\x03")
        assert resp.service_id == 0x1234
        assert resp.method_id == 0x0001
        assert resp.return_code == 0x00
        assert resp.payload == "010203"


@pytest.mark.skipif(not _RUN_INTEGRATION, reason="SOMEIP_INTEGRATION_TESTS not set")
def test_someip_simulated_send_receive(mock_someip_server):
    driver = SomeIp(
        host="127.0.0.1",
        port=mock_someip_server,
        transport_mode="TCP",
    )
    with serve(driver) as client:
        client.send_message(0x1234, 0x0001, b"\xAA\xBB\xCC")
        resp = client.receive_message(timeout=2.0)
        assert resp.service_id == 0x1234
        assert resp.payload == "aabbcc"
