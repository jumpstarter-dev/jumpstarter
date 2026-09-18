"""Tests for NanoKVM-USB driver."""

from io import BytesIO
from unittest.mock import MagicMock

import pytest
from PIL import Image

from .driver import NanoKVMUSB, NanoKVMUSBHID, NanoKVMUSBVideo
from .keyboard import KeyboardReport
from .mouse import MouseButton, resolve_button
from .v4l2_ctl_mjpeg import V4L2CtlMjpegCapture, _extract_jpegs
from jumpstarter.common.utils import serve


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
    video_child = driver.children["video"]
    assert isinstance(video_child, NanoKVMUSBVideo)
    video_child.device = mock_device
    video_child._owns_device = False
    hid_child = driver.children["hid"]
    assert isinstance(hid_child, NanoKVMUSBHID)
    hid_child.device = mock_device
    hid_child._owns_device = False

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


def test_v4l2_ctl_open_rejects_missing_executable():
    cap = V4L2CtlMjpegCapture(v4l2_ctl_executable="/nonexistent/v4l2-ctl")
    with pytest.raises(OSError, match="v4l2-ctl not found"):
        cap.open(0, 640, 480, 30)
    assert not cap.is_open


def test_v4l2_ctl_open_rejects_immediate_exit(tmp_path):
    fake = tmp_path / "fake-v4l2-ctl"
    fake.write_text("#!/bin/sh\nexit 1\n")
    fake.chmod(0o755)

    cap = V4L2CtlMjpegCapture(v4l2_ctl_executable=str(fake))
    with pytest.raises(OSError, match="exited immediately"):
        cap.open("/dev/video0", 640, 480, 30)
    assert not cap.is_open
    assert cap._proc is None


def test_extract_jpegs_soi_split_across_chunks():
    buffer = bytearray()
    assert _extract_jpegs(buffer, b"\xff") == []
    assert buffer == bytearray(b"\xff")
    payload = b"\xd8" + b"\x00" * 4 + b"\xff\xd9"
    frames = _extract_jpegs(buffer, payload)
    assert len(frames) == 1
    assert frames[0].startswith(b"\xff\xd8")
    assert frames[0].endswith(b"\xff\xd9")


def test_bracket_keys_do_not_use_shift():
    kb = KeyboardReport()
    for ch in "[]\\":
        down, _up = kb.char_to_report(ch)
        assert down[0] == 0, f"unexpected shift modifier for {ch!r}"


def test_resolve_button_rejects_unknown():
    with pytest.raises(ValueError, match="Unknown mouse button"):
        resolve_button("not-a-button")


def test_protocol_packet_roundtrip():
    from .protocol import CmdEvent, CmdPacket

    packet = CmdPacket(addr=0x00, cmd=CmdEvent.SEND_KB_GENERAL_DATA, data=[0, 0, 4, 0, 0, 0, 0, 0])
    decoded = CmdPacket.decode(packet.encode())
    assert decoded.addr == packet.addr
    assert decoded.cmd == packet.cmd
    assert decoded.data == packet.data
