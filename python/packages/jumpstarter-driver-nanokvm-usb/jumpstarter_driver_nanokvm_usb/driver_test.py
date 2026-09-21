"""Tests for NanoKVM-USB driver."""

from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from .driver import NanoKVMUSB, NanoKVMUSBHID, NanoKVMUSBVideo, NanoKVMUSBVNC
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
    jpeg = _jpeg_bytes()
    device.capture_frame_jpeg.return_value = jpeg

    def _snapshot_jpeg(skip_frames=3):
        for _ in range(int(skip_frames)):
            device.capture_frame_jpeg()
        return device.capture_frame_jpeg()

    device.snapshot_jpeg.side_effect = _snapshot_jpeg
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
        assert hasattr(client, "vnc")

        image = client.video.snapshot()
        assert isinstance(image, Image.Image)

        client.hid.paste_text("Test")
        mock_device.type_text.assert_called_with("Test")
        assert client.vnc.get_default_encrypt() is False


def test_nanokvm_usb_video_client_creation():
    assert NanoKVMUSBVideo.client() == "jumpstarter_driver_nanokvm_usb.client.NanoKVMUSBVideoClient"


def test_nanokvm_usb_hid_client_creation():
    assert NanoKVMUSBHID.client() == "jumpstarter_driver_nanokvm_usb.client.NanoKVMUSBHIDClient"


def test_nanokvm_usb_client_creation():
    assert NanoKVMUSB.client() == "jumpstarter_driver_nanokvm_usb.client.NanoKVMUSBClient"


def test_nanokvm_usb_vnc_client_creation():
    assert NanoKVMUSBVNC.client() == "jumpstarter_driver_nanokvm_usb.client.NanoKVMUSBVNCClient"


def test_nanokvm_usb_vnc_disabled():
    driver = NanoKVMUSB(serial_port="/dev/null", video_device=0, vnc_enabled=False)
    try:
        assert "vnc" not in driver.children
        assert driver._vnc_server is None
    finally:
        driver.close()


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


def test_frame_pump_fans_out_jpeg():
    from .frame_pump import FramePump

    jpeg = _jpeg_bytes(32, 24)
    pump = FramePump(lambda: jpeg, fps=50)
    pump.start()
    try:
        first = pump.wait_jpeg(timeout=2.0)
        assert first is not None
        data, gen = first
        assert data == jpeg
        second = pump.wait_jpeg(timeout=2.0, after_generation=gen)
        assert second is not None
        assert second[1] > gen
        skipped = pump.wait_n_frames(3, timeout=2.0)
        assert skipped == jpeg
    finally:
        pump.stop()
    assert not pump.is_running


def test_keysym_and_pointer_mapping():
    from .vnc_server import keysym_to_key, rfb_buttons_to_hid

    assert keysym_to_key(ord("a")) == "KeyA"
    assert keysym_to_key(ord("A")) == "KeyA"
    assert keysym_to_key(ord("5")) == "Digit5"
    assert keysym_to_key(0xFF0D) == "Enter"
    assert keysym_to_key(0xFFBE) == "F1"
    assert keysym_to_key(0xFFFF) == "Delete"
    hid, wheel = rfb_buttons_to_hid(0x01)
    assert hid == MouseButton.LEFT
    assert wheel == 0
    hid, wheel = rfb_buttons_to_hid(0x08)
    assert wheel == 1


def test_des_encrypt_nist_vector():
    from .vnc_server import _des_ecb_encrypt, vnc_auth_response

    key = bytes.fromhex("133457799BBCDFF1")
    plain = bytes.fromhex("0123456789ABCDEF")
    assert _des_ecb_encrypt(plain, key) == bytes.fromhex("85E813540F0AB405")
    response = vnc_auth_response(b"\x00" * 16, "secret")
    assert len(response) == 16


def test_rfb_handshake_and_input(tmp_path):
    import socket
    import struct
    import time

    from .frame_pump import FramePump
    from .vnc_server import RFB_VERSION, RfbServer, _recvexact

    jpeg = _jpeg_bytes(64, 48)
    pump = FramePump(lambda: jpeg, fps=20)
    pump.start()
    hid = MagicMock()
    sock_path = str(tmp_path / "vnc.sock")
    server = RfbServer(sock_path, pump, hid, width=64, height=48)
    server.start()
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not Path(sock_path).exists():
            time.sleep(0.01)
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(5)
        client.connect(sock_path)
        assert _recvexact(client, 12) == RFB_VERSION
        client.sendall(RFB_VERSION)
        header = _recvexact(client, 2)
        assert header[0] == 1
        assert header[1] == 1
        client.sendall(bytes([1]))
        assert struct.unpack("!I", _recvexact(client, 4))[0] == 0
        client.sendall(b"\x01")  # ClientInit
        server_init = _recvexact(client, 20)
        width, height = struct.unpack("!HH", server_init[:4])
        assert (width, height) == (64, 48)
        name_len = struct.unpack("!I", _recvexact(client, 4))[0]
        assert _recvexact(client, name_len) == b"NanoKVM-USB"
        client.sendall(b"\x02\x00" + struct.pack("!H", 1) + struct.pack("!i", 0))
        client.sendall(b"\x03\x00" + struct.pack("!HHHH", 0, 0, 64, 48))
        # KeyEvent: down, pad, keysym 'a'
        client.sendall(b"\x04\x01\x00\x00" + struct.pack("!I", ord("a")))
        # PointerEvent: left button at 32,24
        client.sendall(b"\x05\x01" + struct.pack("!HH", 32, 24))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not hid.hid_key.called:
            time.sleep(0.02)
        hid.hid_key.assert_called_with("KeyA", True)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not hid.mouse_pointer.called:
            time.sleep(0.02)
        args = hid.mouse_pointer.call_args[0]
        assert args[2] == MouseButton.LEFT
        client.close()
    finally:
        server.stop()
        pump.stop()


def test_rfb_vnc_auth(tmp_path):
    import socket
    import struct
    import time

    from .frame_pump import FramePump
    from .vnc_server import RFB_VERSION, RfbServer, _recvexact, vnc_auth_response

    jpeg = _jpeg_bytes(16, 16)
    pump = FramePump(lambda: jpeg, fps=10)
    pump.start()
    sock_path = str(tmp_path / "vnc-auth.sock")
    server = RfbServer(sock_path, pump, MagicMock(), width=16, height=16, password="secret")
    server.start()
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not Path(sock_path).exists():
            time.sleep(0.01)
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(5)
        client.connect(sock_path)
        assert _recvexact(client, 12) == RFB_VERSION
        client.sendall(RFB_VERSION)
        header = _recvexact(client, 2)
        assert header == bytes([1, 2])
        client.sendall(bytes([2]))
        challenge = _recvexact(client, 16)
        client.sendall(vnc_auth_response(challenge, "secret"))
        assert struct.unpack("!I", _recvexact(client, 4))[0] == 0
        client.close()
    finally:
        server.stop()
        pump.stop()


def test_is_loopback_bind():
    from .vnc_server import is_loopback_bind

    assert is_loopback_bind("127.0.0.1")
    assert is_loopback_bind("localhost")
    assert not is_loopback_bind("0.0.0.0")
    assert not is_loopback_bind("192.168.1.10")


def test_rfb_tcp_handshake(tmp_path):
    import socket
    import struct
    import time

    from .frame_pump import FramePump
    from .vnc_server import RFB_VERSION, RfbServer, _recvexact

    jpeg = _jpeg_bytes(16, 16)
    pump = FramePump(lambda: jpeg, fps=10)
    pump.start()
    sock_path = str(tmp_path / "vnc-tcp.sock")
    server = RfbServer(
        sock_path,
        pump,
        MagicMock(),
        width=16,
        height=16,
        tcp_port=0,
        tcp_bind="127.0.0.1",
    )
    server.start()
    try:
        endpoint = server.tcp_endpoint
        assert endpoint is not None
        host, port = endpoint
        deadline = time.monotonic() + 2
        client = None
        while time.monotonic() < deadline:
            try:
                client = socket.create_connection((host, port), timeout=2)
                break
            except OSError:
                time.sleep(0.01)
        assert client is not None
        client.settimeout(5)
        assert _recvexact(client, 12) == RFB_VERSION
        client.sendall(RFB_VERSION)
        header = _recvexact(client, 2)
        assert header == bytes([1, 1])
        client.sendall(bytes([1]))
        assert struct.unpack("!I", _recvexact(client, 4))[0] == 0
        client.sendall(b"\x01")
        server_init = _recvexact(client, 20)
        width, height = struct.unpack("!HH", server_init[:4])
        assert (width, height) == (16, 16)
        name_len = struct.unpack("!I", _recvexact(client, 4))[0]
        assert _recvexact(client, name_len) == b"NanoKVM-USB"
        client.close()
    finally:
        server.stop()
        pump.stop()
