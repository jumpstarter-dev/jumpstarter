"""High-level NanoKVM-USB device API combining serial HID and UVC video."""

from __future__ import annotations

import threading
import time

from .frame_pump import FramePump
from .keyboard import KeyboardReport, resolve_key_code
from .mouse import (
    MouseButton,
    build_absolute_report,
    build_relative_report,
    resolve_button,
)
from .protocol import CmdEvent, CmdPacket, InfoPacket
from .serial_conn import SerialConnection
from .video import VideoCapture

INTER_KEY_DELAY = 0.05
KEY_HOLD_DELAY = 0.02


class NanoKVMUSBDevice:
    """Unified interface to a NanoKVM-USB device over serial and UVC."""

    def __init__(
        self,
        serial_port: str,
        baud_rate: int = 57600,
        video_device: int | str | None = 0,
        video_width: int = 1920,
        video_height: int = 1080,
        video_fps: int = 30,
        video_format: str = "mjpeg_passthrough",
        video_jpeg_quality: int = 95,
        video_discard_stale: int = 1,
        v4l2_ctl_executable: str | None = None,
        screen_width: int = 1920,
        screen_height: int = 1080,
    ) -> None:
        self._serial_port_path = serial_port
        self._baud_rate = baud_rate
        self._video_device = video_device
        self._video_width = video_width
        self._video_height = video_height
        self._video_fps = video_fps
        self._video_format = video_format
        self._video_jpeg_quality = video_jpeg_quality
        self._video_discard_stale = max(0, int(video_discard_stale))
        self._v4l2_ctl_executable = v4l2_ctl_executable
        self.screen_width = screen_width
        self.screen_height = screen_height

        self._serial = SerialConnection()
        self._video = VideoCapture()
        self._keyboard = KeyboardReport()
        self._addr = 0x00
        self._buttons = 0
        self._connected = False
        self._connect_lock = threading.RLock()
        self._hid_lock = threading.RLock()
        self._pump: FramePump | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected and self._serial.is_open

    @property
    def has_video(self) -> bool:
        return self._video.is_open

    def ensure_connected(self) -> InfoPacket | None:
        """Connect on first use; serialized across video/HID child drivers."""
        with self._connect_lock:
            if self.is_connected:
                return None
            return self.connect()

    def connect(self) -> InfoPacket | None:
        with self._connect_lock:
            try:
                self._serial.open(self._serial_port_path, self._baud_rate)
                info = self.get_info()

                if self._video_device is not None:
                    self._video.open(
                        self._video_device,
                        self._video_width,
                        self._video_height,
                        self._video_fps,
                        video_format=self._video_format,
                        jpeg_quality=self._video_jpeg_quality,
                        v4l2_ctl_executable=self._v4l2_ctl_executable,
                    )

                self._connected = True
                if self._video_device is not None:
                    if self._pump is None:
                        self._pump = FramePump(self.capture_frame_jpeg, fps=self._video_fps)
                    self._pump.start()
                return info
            except Exception:
                self.close()
                raise

    def close(self) -> None:
        with self._connect_lock:
            if self._pump is not None:
                self._pump.stop()
            self._serial.close()
            self._video.close()
            self._connected = False

    def get_info(self) -> InfoPacket:
        packet = CmdPacket(addr=self._addr, cmd=CmdEvent.GET_INFO)
        self._serial.write(packet.encode())
        response = self._serial.read(14)
        response_packet = CmdPacket.decode(response)
        return InfoPacket.from_data(response_packet.data)

    def _send_keyboard(self, report: list[int]) -> None:
        packet = CmdPacket(addr=self._addr, cmd=CmdEvent.SEND_KB_GENERAL_DATA, data=report)
        self._serial.write(packet.encode())

    def press_key(self, key: str, hold: float = KEY_HOLD_DELAY) -> None:
        with self._hid_lock:
            code = resolve_key_code(key)
            report = self._keyboard.key_down(code)
            self._send_keyboard(report)
            time.sleep(hold)
            report = self._keyboard.key_up(code)
            self._send_keyboard(report)

    def hid_key(self, key: str, down: bool) -> None:
        """Press or release a named key (used by the RFB server)."""
        with self._hid_lock:
            code = resolve_key_code(key)
            report = self._keyboard.key_down(code) if down else self._keyboard.key_up(code)
            self._send_keyboard(report)

    def hid_char(self, key: str, modifiers: frozenset[str], down: bool) -> None:
        """Type a printable character using DUT-layout HID modifiers (RFB)."""
        with self._hid_lock:
            report = self._keyboard.printable_down(key, modifiers) if down else self._keyboard.printable_up()
            self._send_keyboard(report)

    def release_all_keys(self) -> None:
        with self._hid_lock:
            report = self._keyboard.reset()
            self._send_keyboard(report)

    def type_text(self, text: str, delay: float = INTER_KEY_DELAY) -> None:
        with self._hid_lock:
            for ch in text:
                down, up = self._keyboard.char_to_report(ch)
                self._send_keyboard(down)
                time.sleep(KEY_HOLD_DELAY)
                self._send_keyboard(up)
                time.sleep(delay)

    def _send_mouse(self, report: list[int]) -> None:
        cmd = CmdEvent.SEND_MS_REL_DATA if report[0] == 0x01 else CmdEvent.SEND_MS_ABS_DATA
        packet = CmdPacket(addr=self._addr, cmd=cmd, data=report)
        self._serial.write(packet.encode())

    def mouse_move_abs(self, x: float, y: float) -> None:
        with self._hid_lock:
            report = build_absolute_report(x, y, buttons=self._buttons)
            self._send_mouse(report)

    def mouse_pointer(self, x: float, y: float, buttons: int, wheel: int = 0) -> None:
        """Absolute pointer update from the RFB server."""
        with self._hid_lock:
            self._buttons = buttons & 0x1F
            report = build_absolute_report(x, y, buttons=self._buttons, wheel=wheel)
            self._send_mouse(report)

    def mouse_move_to(self, x: float, y: float) -> None:
        with self._hid_lock:
            self._mouse_move_relative_locked(-self.screen_width * 2, -self.screen_height * 2)
            target_x = int(x * self.screen_width)
            target_y = int(y * self.screen_height)
            self._mouse_move_relative_locked(target_x, target_y)

    def mouse_move_relative(self, dx: int, dy: int, step_delay: float = 0.005) -> None:
        with self._hid_lock:
            self._mouse_move_relative_locked(dx, dy, step_delay)

    def _mouse_move_relative_locked(self, dx: int, dy: int, step_delay: float = 0.005) -> None:
        while dx != 0 or dy != 0:
            chunk_x = max(-127, min(127, dx))
            chunk_y = max(-127, min(127, dy))
            report = build_relative_report(dx=chunk_x, dy=chunk_y, buttons=self._buttons)
            self._send_mouse(report)
            dx -= chunk_x
            dy -= chunk_y
            if dx != 0 or dy != 0:
                time.sleep(step_delay)

    def mouse_click(
        self,
        button: MouseButton | str | int = "left",
        x: float | None = None,
        y: float | None = None,
        hold: float = 0.05,
    ) -> None:
        btn_bit = resolve_button(button)

        with self._hid_lock:
            if x is not None and y is not None:
                self._mouse_move_relative_locked(-self.screen_width * 2, -self.screen_height * 2)
                self._mouse_move_relative_locked(int(x * self.screen_width), int(y * self.screen_height))
                self._buttons |= btn_bit
                report = build_absolute_report(x, y, buttons=self._buttons)
                self._send_mouse(report)
                time.sleep(hold)
                self._buttons &= ~btn_bit
                report = build_absolute_report(x, y, buttons=self._buttons)
                self._send_mouse(report)
            else:
                self._buttons |= btn_bit
                report = build_relative_report(buttons=self._buttons)
                self._send_mouse(report)
                time.sleep(hold)
                self._buttons &= ~btn_bit
                report = build_relative_report(buttons=self._buttons)
                self._send_mouse(report)

    def mouse_scroll(self, dx: int, dy: int) -> None:
        with self._hid_lock:
            wheel = dy if dy != 0 else dx
            report = build_relative_report(wheel=wheel, buttons=self._buttons)
            self._send_mouse(report)

    def mouse_reset(self) -> None:
        with self._hid_lock:
            self._buttons = 0
            report = build_relative_report(buttons=0)
            self._send_mouse(report)

    def reset_hid(self) -> None:
        self.release_all_keys()
        self.mouse_reset()

    def capture_frame_jpeg(self, quality: int | None = None) -> bytes:
        q = quality if quality is not None else self._video_jpeg_quality
        if self._video_discard_stale > 0:
            self._video.discard_stale_frames(self._video_discard_stale)
        return self._video.read_frame_jpeg(q)

    def snapshot_jpeg(self, skip_frames: int = 3) -> bytes:
        if self._pump is not None and self._pump.is_running:
            return self._pump.wait_n_frames(max(1, int(skip_frames) + 1))
        for _ in range(max(0, int(skip_frames))):
            self.capture_frame_jpeg()
        return self.capture_frame_jpeg()

    def __enter__(self) -> NanoKVMUSBDevice:
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
