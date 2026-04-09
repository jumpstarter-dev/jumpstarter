import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .driver import BleWriteNotifyStream, _ble_notify_handler
from jumpstarter.common.utils import serve

TEST_ADDRESS = "AA:BB:CC:DD:EE:FF"
TEST_SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
TEST_WRITE_CHAR_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
TEST_NOTIFY_CHAR_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"


def _make_driver():
    return BleWriteNotifyStream(
        address=TEST_ADDRESS,
        service_uuid=TEST_SERVICE_UUID,
        write_char_uuid=TEST_WRITE_CHAR_UUID,
        notify_char_uuid=TEST_NOTIFY_CHAR_UUID,
    )


def _make_mock_bleak_client(is_connected=True, characteristics=None):
    """Create a mock BleakClient with configurable services and characteristics."""
    mock_client = AsyncMock()
    mock_client.is_connected = is_connected

    # Create mock characteristic objects
    if characteristics is None:
        write_char = MagicMock()
        write_char.uuid = TEST_WRITE_CHAR_UUID
        notify_char = MagicMock()
        notify_char.uuid = TEST_NOTIFY_CHAR_UUID
        characteristics = [write_char, notify_char]

    # Create mock service
    mock_service = MagicMock()
    mock_service.uuid = TEST_SERVICE_UUID
    mock_service.characteristics = characteristics

    mock_client.services = [mock_service]
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    return mock_client


def test_ble_driver_info():
    """Test the info export returns correct device information via e2e server/client."""
    mock_client = _make_mock_bleak_client()

    with patch("jumpstarter_driver_ble.driver.BleakClient", return_value=mock_client):
        with serve(_make_driver()) as client:
            info = client.call("info")
            assert TEST_ADDRESS in info
            assert TEST_SERVICE_UUID in info
            assert TEST_WRITE_CHAR_UUID in info
            assert TEST_NOTIFY_CHAR_UUID in info


def test_ble_driver_connect_stream():
    """Test connecting to a BLE device and exchanging data via the stream e2e."""
    mock_client = _make_mock_bleak_client()

    mock_client.write_gatt_char = AsyncMock()

    with patch("jumpstarter_driver_ble.driver.BleakClient", return_value=mock_client):
        with serve(_make_driver()) as client:
            with client.stream() as stream:
                # Send data through the stream
                stream.send(b"hello")
                mock_client.write_gatt_char.assert_called()

                # Verify start_notify was called for the notify characteristic
                mock_client.start_notify.assert_called_once()
                call_args = mock_client.start_notify.call_args
                assert call_args[0][0] == TEST_NOTIFY_CHAR_UUID


def test_ble_notify_handler():
    """Test the notification handler puts data into the queue."""
    queue = asyncio.Queue(maxsize=10)
    sender = MagicMock()
    test_data = bytearray(b"test_notification")

    _ble_notify_handler(sender, test_data, queue)

    assert not queue.empty()
    assert queue.get_nowait() == test_data


def test_ble_notify_handler_queue_full(capsys):
    """Test the notification handler handles a full queue gracefully."""
    queue = asyncio.Queue(maxsize=1)
    sender = MagicMock()

    # Fill the queue
    queue.put_nowait(b"first")

    # This should print a warning, not raise
    _ble_notify_handler(sender, bytearray(b"second"), queue)

    captured = capsys.readouterr()
    assert "queue is full" in captured.out


@pytest.mark.asyncio
async def test_ble_check_characteristics_missing_service():
    """Test that _check_ble_characteristics raises when service UUID is not found."""
    from bleak.exc import BleakError

    driver = _make_driver()

    mock_client = AsyncMock()
    mock_service = MagicMock()
    mock_service.uuid = "00000000-0000-0000-0000-000000000000"  # wrong UUID
    mock_service.characteristics = []
    mock_client.services = [mock_service]

    with pytest.raises(BleakError, match="Service UUID"):
        await driver._check_ble_characteristics(mock_client)


@pytest.mark.asyncio
async def test_ble_check_characteristics_missing_write_char():
    """Test that _check_ble_characteristics raises when write characteristic is missing."""
    from bleak.exc import BleakError

    driver = _make_driver()

    notify_char = MagicMock()
    notify_char.uuid = TEST_NOTIFY_CHAR_UUID

    mock_service = MagicMock()
    mock_service.uuid = TEST_SERVICE_UUID
    mock_service.characteristics = [notify_char]

    mock_client = AsyncMock()
    mock_client.services = [mock_service]

    with pytest.raises(BleakError, match="Write characteristic UUID"):
        await driver._check_ble_characteristics(mock_client)


@pytest.mark.asyncio
async def test_ble_check_characteristics_missing_notify_char():
    """Test that _check_ble_characteristics raises when notify characteristic is missing."""
    from bleak.exc import BleakError

    driver = _make_driver()

    write_char = MagicMock()
    write_char.uuid = TEST_WRITE_CHAR_UUID

    mock_service = MagicMock()
    mock_service.uuid = TEST_SERVICE_UUID
    mock_service.characteristics = [write_char]

    mock_client = AsyncMock()
    mock_client.services = [mock_service]

    with pytest.raises(BleakError, match="Notify characteristic UUID"):
        await driver._check_ble_characteristics(mock_client)


@pytest.mark.asyncio
async def test_ble_check_characteristics_success():
    """Test that _check_ble_characteristics succeeds with correct characteristics."""
    driver = _make_driver()
    mock_client = _make_mock_bleak_client()

    # Should not raise
    await driver._check_ble_characteristics(mock_client)


def test_ble_driver_connect_not_connected():
    """Test that connect raises when client fails to connect."""
    mock_client = _make_mock_bleak_client(is_connected=False)

    with patch("jumpstarter_driver_ble.driver.BleakClient", return_value=mock_client):
        with serve(_make_driver()) as client:
            raised = False
            try:
                with client.stream() as stream:
                    stream.send(b"hello")
                    stream.receive()
            except BaseException:
                raised = True
            assert raised, "Expected an exception when BLE device is not connected"


def test_ble_driver_client_class_reference():
    """Test that the driver correctly references the client class."""
    assert BleWriteNotifyStream.client() == "jumpstarter_driver_ble.client.BleWriteNotifyStreamClient"
