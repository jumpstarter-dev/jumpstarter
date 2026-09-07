"""Tests for NanoKVM-USB driver."""

from io import BytesIO
from unittest.mock import MagicMock

import pytest
from PIL import Image

from jumpstarter.common.utils import serve

from .driver import NanoKVMUSB, NanoKVMUSBHID, NanoKVMUSBVideo
from .mouse import MouseButton


def _jpeg_bytes(width: int = 640, height: int = 480) -> bytes:
    image = Image.new("RGB", (width, height), color="red")
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def mock_device():
    device = MagicMock()
    device.is_connected = True
    device.screen_width = 1920
    device.screen_height = 1080
    device.capture_frame_jpeg.return_value = _jpeg_bytes()
    return device


def test_nanokvm_usb_video_snapshot(mock_device):
    video = NanoKVMUSBVideo(device=mock_device, serial_port="/dev/null")

    with serve(video) as client:
        image = client.snapshot()
        assert isinstance(image, Image.Image)
        assert image.size == (640, 480)
        assert mock_device.capture_frame_jpeg.call_count >= 3


def test_nanokvm_usb_hid_paste(mock_device):
    hid = NanoKVMUSBHID(device=mock_device, serial_port="/dev/null", video_device=None)

    with serve(hid) as client:
        client.paste_text("Hello, World!")
        mock_device.type_text.assert_called_once_with("Hello, World!")


def test_nanokvm_usb_hid_reset(mock_device):
    hid = NanoKVMUSBHID(device=mock_device, serial_port="/dev/null", video_device=None)

    with serve(hid) as client:
        client.reset_hid()
        mock_device.reset_hid.assert_called_once()


def test_nanokvm_usb_hid_press_key(mock_device):
    hid = NanoKVMUSBHID(device=mock_device, serial_port="/dev/null", video_device=None)

    with serve(hid) as client:
        client.press_key("a")
        mock_device.type_text.assert_called_with("a")


def test_nanokvm_usb_composite(mock_device):
    driver = NanoKVMUSB(
        serial_port="/dev/null",
        video_device=0,
    )
    driver._shared_device = mock_device
    driver.children["video"].device = mock_device
    driver.children["video"]._owns_device = False
    driver.children["hid"].device = mock_device
    driver.children["hid"]._owns_device = False

    with serve(driver) as client:
        assert hasattr(client, "video")
        assert hasattr(client, "hid")

        image = client.video.snapshot()
        assert isinstance(image, Image.Image)

        client.hid.paste_text("Test")
        mock_device.type_text.assert_called_with("Test")


def test_nanokvm_usb_video_client_creation():
    assert NanoKVMUSBVideo.client() == "jumpstarter_driver_nanokvm_usb.client.NanoKVMUSBVideoClient"


def test_nanokvm_usb_hid_client_creation():
    assert NanoKVMUSBHID.client() == "jumpstarter_driver_nanokvm_usb.client.NanoKVMUSBHIDClient"


def test_nanokvm_usb_client_creation():
    assert NanoKVMUSB.client() == "jumpstarter_driver_nanokvm_usb.client.NanoKVMUSBClient"


def test_nanokvm_usb_mouse_move_abs(mock_device):
    hid = NanoKVMUSBHID(device=mock_device, serial_port="/dev/null", video_device=None)

    with serve(hid) as client:
        client.mouse_move_abs(0.5, 0.5)
        mock_device.mouse_move_abs.assert_called_once_with(0.5, 0.5)


def test_nanokvm_usb_mouse_click(mock_device):
    hid = NanoKVMUSBHID(device=mock_device, serial_port="/dev/null", video_device=None)

    with serve(hid) as client:
        client.mouse_click("left")
        mock_device.mouse_click.assert_called_once_with(MouseButton.LEFT, None, None)


def test_protocol_packet_roundtrip():
    from .protocol import CmdEvent, CmdPacket

    packet = CmdPacket(addr=0x00, cmd=CmdEvent.SEND_KB_GENERAL_DATA, data=[0, 0, 4, 0, 0, 0, 0, 0])
    decoded = CmdPacket.decode(packet.encode())
    assert decoded.addr == packet.addr
    assert decoded.cmd == packet.cmd
    assert decoded.data == packet.data
