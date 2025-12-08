"""Tests for NanoKVM driver"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from .driver import NanoKVM, NanoKVMHID, NanoKVMVideo
from jumpstarter.common.utils import serve


@pytest.fixture
def mock_nanokvm_client():
    """Create a mock NanoKVM API client"""
    with patch("jumpstarter_driver_nanokvm.driver.NanoKVMAPIClient") as mock_client_class:
        mock_client = AsyncMock()

        # Mock authentication
        mock_client.authenticate = AsyncMock()
        mock_client.logout = AsyncMock()

        # Mock info
        mock_info = MagicMock()
        mock_info.ips = []
        mock_info.mdns = "nanokvm-test.local"
        mock_info.image = "1.0.0"
        mock_info.application = "1.0.0"
        mock_info.device_key = "test-key"
        mock_client.get_info = AsyncMock(return_value=mock_info)

        # Mock video streaming
        test_image = Image.new("RGB", (640, 480), color="red")

        async def mock_stream():
            # Yield several frames - first ones are buffered/old, later ones are fresh
            yield test_image
            yield test_image
            yield test_image
            yield test_image

        mock_client.mjpeg_stream = mock_stream

        # Mock HID functions
        mock_client.paste_text = AsyncMock()
        mock_client.reset_hid = AsyncMock()

        # Mock reboot
        mock_client.reboot_system = AsyncMock()

        mock_client_class.return_value = mock_client
        yield mock_client


@pytest.fixture
def mock_aiohttp_session():
    """Create a mock aiohttp ClientSession"""
    with patch("jumpstarter_driver_nanokvm.driver.ClientSession") as mock_session_class:
        mock_session = AsyncMock()
        mock_session.close = AsyncMock()
        mock_session_class.return_value = mock_session
        yield mock_session


def test_nanokvm_video_snapshot(mock_nanokvm_client, mock_aiohttp_session):
    """Test video snapshot functionality"""
    video = NanoKVMVideo(host="test.local", username="admin", password="admin")

    with serve(video) as client:
        # Get a snapshot
        image = client.snapshot()

        # Verify it's a PIL Image
        assert isinstance(image, Image.Image)
        assert image.size == (640, 480)


def test_nanokvm_hid_paste(mock_nanokvm_client, mock_aiohttp_session):
    """Test HID paste text functionality"""
    hid = NanoKVMHID(host="test.local", username="admin", password="admin")

    with serve(hid) as client:
        # Paste some text
        client.paste_text("Hello, World!")

        # Verify the mock was called
        mock_nanokvm_client.paste_text.assert_called_once_with("Hello, World!")


def test_nanokvm_hid_reset(mock_nanokvm_client, mock_aiohttp_session):
    """Test HID reset functionality"""
    hid = NanoKVMHID(host="test.local", username="admin", password="admin")

    with serve(hid) as client:
        # Reset HID
        client.reset_hid()

        # Verify the mock was called
        mock_nanokvm_client.reset_hid.assert_called_once()


def test_nanokvm_hid_press_key(mock_nanokvm_client, mock_aiohttp_session):
    """Test key press functionality"""
    hid = NanoKVMHID(host="test.local", username="admin", password="admin")

    with serve(hid) as client:
        # Press a key
        client.press_key("a")

        # Verify paste_text was called with the character
        mock_nanokvm_client.paste_text.assert_called_with("a")


def test_nanokvm_composite(mock_nanokvm_client, mock_aiohttp_session):
    """Test composite NanoKVM driver"""
    driver = NanoKVM(
        host="test.local",
        username="admin",
        password="admin",
    )

    with serve(driver) as client:
        # Test that children are accessible
        assert hasattr(client, "video")
        assert hasattr(client, "hid")

        # Test video snapshot through composite
        image = client.video.snapshot()
        assert isinstance(image, Image.Image)

        # Test HID paste through composite
        client.hid.paste_text("Test")
        mock_nanokvm_client.paste_text.assert_called_with("Test")

        # Test get_info
        info = client.get_info()
        assert "mdns" in info
        assert info["mdns"] == "nanokvm-test.local"


def test_nanokvm_reboot(mock_nanokvm_client, mock_aiohttp_session):
    """Test NanoKVM reboot functionality"""
    driver = NanoKVM(
        host="test.local",
        username="admin",
        password="admin",
    )

    with serve(driver) as client:
        # Test reboot
        client.reboot()
        mock_nanokvm_client.reboot_system.assert_called_once()


def test_nanokvm_video_client_creation():
    """Test that NanoKVMVideo returns correct client class"""
    assert NanoKVMVideo.client() == "jumpstarter_driver_nanokvm.client.NanoKVMVideoClient"


def test_nanokvm_hid_client_creation():
    """Test that NanoKVMHID returns correct client class"""
    assert NanoKVMHID.client() == "jumpstarter_driver_nanokvm.client.NanoKVMHIDClient"


def test_nanokvm_client_creation():
    """Test that NanoKVM returns correct client class"""
    assert NanoKVM.client() == "jumpstarter_driver_nanokvm.client.NanoKVMClient"


def test_nanokvm_mouse_move_abs(mock_nanokvm_client, mock_aiohttp_session):
    """Test mouse absolute movement"""
    with patch("jumpstarter_driver_nanokvm.driver.ClientSession") as mock_session_class:
        mock_ws = AsyncMock()
        mock_ws.send_json = AsyncMock()
        mock_session = AsyncMock()
        mock_session.ws_connect = AsyncMock(return_value=mock_ws)
        mock_session.close = AsyncMock()
        mock_session_class.return_value = mock_session

        hid = NanoKVMHID(host="test.local", username="admin", password="admin")

        with serve(hid) as client:
            # Move mouse to absolute position
            client.mouse_move_abs(32768, 32768)

            # Verify WebSocket message was sent
            mock_ws.send_json.assert_called()


def test_nanokvm_mouse_click(mock_nanokvm_client, mock_aiohttp_session):
    """Test mouse click"""
    with patch("jumpstarter_driver_nanokvm.driver.ClientSession") as mock_session_class:
        mock_ws = AsyncMock()
        mock_ws.send_json = AsyncMock()
        mock_session = AsyncMock()
        mock_session.ws_connect = AsyncMock(return_value=mock_ws)
        mock_session.close = AsyncMock()
        mock_session_class.return_value = mock_session

        hid = NanoKVMHID(host="test.local", username="admin", password="admin")

        with serve(hid) as client:
            # Click left button
            client.mouse_click("left")

            # Verify WebSocket messages were sent (down and up)
            assert mock_ws.send_json.call_count >= 2
