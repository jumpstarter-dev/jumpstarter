import shutil
import tempfile
from base64 import b64encode
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import anyio
from anyio import to_thread
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_network.driver import UnixNetwork

from .device import NanoKVMUSBDevice
from .frame_pump import FramePump
from .keyboard import resolve_key_code
from .mouse import MouseButton, resolve_button
from .vnc_server import RfbServer, is_loopback_bind
from jumpstarter.driver import Driver, export, exportstream

__all__ = [
    "NanoKVMUSBVideo",
    "NanoKVMUSBHID",
    "NanoKVMUSB",
    "NanoKVMUSBVNC",
    "MouseButton",
]


@dataclass(kw_only=True)
class NanoKVMUSBDriverBase(Driver):
    """Base class for NanoKVM-USB drivers with shared device management."""

    serial_port: str = ""
    baud_rate: int = 57600
    video_device: int | str | None = 0
    video_width: int = 1920
    video_height: int = 1080
    video_fps: int = 30
    video_format: str = "mjpeg_passthrough"
    video_jpeg_quality: int = 95
    video_discard_stale: int = 1
    video_stream_buffer_size: int = 32
    v4l2_ctl_executable: str | None = None
    screen_width: int = 1920
    screen_height: int = 1080
    device: NanoKVMUSBDevice | None = None
    _owns_device: bool = field(init=False, repr=False, default=False)

    def __post_init__(self):
        if self.device is None:
            if not self.serial_port:
                raise ValueError("serial_port is required when device is not provided")
            self.device = NanoKVMUSBDevice(
                serial_port=self.serial_port,
                baud_rate=self.baud_rate,
                video_device=self.video_device,
                video_width=self.video_width,
                video_height=self.video_height,
                video_fps=self.video_fps,
                video_format=self.video_format,
                video_jpeg_quality=self.video_jpeg_quality,
                video_discard_stale=self.video_discard_stale,
                v4l2_ctl_executable=self.v4l2_ctl_executable,
                screen_width=self.screen_width,
                screen_height=self.screen_height,
            )
            self._owns_device = True
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

    async def _ensure_device(self) -> NanoKVMUSBDevice:
        assert self.device is not None
        await to_thread.run_sync(self.device.ensure_connected)
        return self.device

    def close(self):
        if self._owns_device and self.device is not None:
            try:
                self.device.close()
            except Exception as exc:
                self.logger.debug(f"Error closing device: {exc}")


@dataclass(kw_only=True)
class NanoKVMUSBVideo(NanoKVMUSBDriverBase):
    """NanoKVM-USB video capture driver."""

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_nanokvm_usb.client.NanoKVMUSBVideoClient"

    @export
    async def snapshot(self, skip_frames: int = 3) -> str:
        """Take a snapshot from the UVC video device."""
        skip_frames = int(skip_frames)

        def _capture() -> bytes:
            device = self.device
            assert device is not None
            device.ensure_connected()
            return device.snapshot_jpeg(skip_frames)

        device = await self._ensure_device()
        self.device = device
        data = await to_thread.run_sync(_capture)
        self.logger.debug(f"snapshot: {len(data)} bytes")
        return b64encode(data).decode("ascii")

    @exportstream
    @asynccontextmanager
    async def stream(self):
        """Stream video frames as JPEG images."""
        self.logger.debug("Starting video stream")
        await self._ensure_device()

        buffer_size = max(1, int(self.video_stream_buffer_size))
        send_stream, receive_stream = anyio.create_memory_object_stream(max_buffer_size=buffer_size)

        async def stream_video():
            device = self.device
            assert device is not None
            state = {"gen": -1}

            def _next_frame() -> bytes:
                pump = getattr(device, "_pump", None)
                if isinstance(pump, FramePump):
                    gen = state["gen"]
                    got = pump.wait_jpeg(
                        timeout=2.0,
                        after_generation=gen if gen >= 0 else None,
                    )
                    if got is None:
                        raise ConnectionError("Timed out waiting for video frame")
                    jpeg, state["gen"] = got
                    return jpeg
                return device.capture_frame_jpeg(self.video_jpeg_quality)

            async with send_stream:
                while True:
                    data = await to_thread.run_sync(_next_frame)
                    try:
                        await send_stream.send(data)
                    except anyio.BrokenResourceError:
                        break

        async with anyio.create_task_group() as tg:
            tg.start_soon(stream_video)
            try:
                yield receive_stream
            finally:
                tg.cancel_scope.cancel()


@dataclass(kw_only=True)
class NanoKVMUSBHID(NanoKVMUSBDriverBase):
    """NanoKVM-USB HID (keyboard/mouse) driver."""

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_nanokvm_usb.client.NanoKVMUSBHIDClient"

    @export
    async def paste_text(self, text: str):
        device = await self._ensure_device()
        await to_thread.run_sync(device.type_text, text)
        self.logger.info("Pasted HID text (%d characters)", len(text))

    @export
    async def press_key(self, key: str):
        if len(key) > 2:
            try:
                resolve_key_code(key)
            except ValueError:
                self.logger.warning(f"press_key should be used with single characters, got: {key}")

        device = await self._ensure_device()

        def _press() -> None:
            if key in ("\n", "\t") or len(key) == 1:
                if key == "\n":
                    device.press_key("enter")
                elif key == "\t":
                    device.press_key("tab")
                else:
                    device.type_text(key)
            else:
                device.press_key(key)

        await to_thread.run_sync(_press)
        self.logger.debug(f"Pressed key: {repr(key)}")

    @export
    async def reset_hid(self):
        device = await self._ensure_device()
        await to_thread.run_sync(device.reset_hid)
        self.logger.info("HID subsystem reset")

    @export
    async def mouse_move_abs(self, x: float, y: float):
        device = await self._ensure_device()
        await to_thread.run_sync(device.mouse_move_abs, x, y)
        self.logger.debug(f"Mouse moved to absolute position: ({x}, {y})")

    @export
    async def mouse_move_rel(self, dx: float, dy: float):
        device = await self._ensure_device()
        pixel_dx = int(dx * device.screen_width)
        pixel_dy = int(dy * device.screen_height)
        await to_thread.run_sync(device.mouse_move_relative, pixel_dx, pixel_dy)
        self.logger.debug(f"Mouse moved by relative offset: ({dx}, {dy})")

    @export
    async def mouse_click(
        self,
        button: MouseButton | str = "left",
        x: float | None = None,
        y: float | None = None,
    ):
        button_label = button if isinstance(button, str) else getattr(button, "name", str(button))
        resolved = resolve_button(button) if isinstance(button, str) else int(button)

        device = await self._ensure_device()
        await to_thread.run_sync(device.mouse_click, resolved, x, y)
        self.logger.info("Mouse %s clicked", button_label)

    @export
    async def mouse_scroll(self, dx: int, dy: int):
        device = await self._ensure_device()
        await to_thread.run_sync(device.mouse_scroll, dx, dy)
        self.logger.debug(f"Mouse scrolled: ({dx}, {dy})")


@dataclass(kw_only=True)
class NanoKVMUSBVNC(UnixNetwork):
    """Unix-socket RFB endpoint with a noVNC session client."""

    default_encrypt: bool = False

    @export
    async def get_default_encrypt(self) -> bool:
        return self.default_encrypt

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_nanokvm_usb.client.NanoKVMUSBVNCClient"


@dataclass(kw_only=True)
class NanoKVMUSB(Composite):
    """
    Composite driver for NanoKVM-USB devices.

    Provides video capture, HID control, and an embedded RFB/VNC endpoint
    over USB serial + UVC.
    """

    serial_port: str
    baud_rate: int = 57600
    video_device: int | str | None = 0
    video_width: int = 1920
    video_height: int = 1080
    video_fps: int = 30
    video_format: str = "mjpeg_passthrough"
    video_jpeg_quality: int = 95
    video_discard_stale: int = 1
    video_stream_buffer_size: int = 32
    v4l2_ctl_executable: str | None = None
    screen_width: int = 1920
    screen_height: int = 1080
    vnc_enabled: bool = True
    vnc_password: str | None = None
    vnc_tcp_port: int | None = None
    vnc_tcp_bind: str = "127.0.0.1"
    vnc_encrypt: bool = False
    vnc_layout: str = "us"

    _shared_device: NanoKVMUSBDevice = field(init=False, repr=False)
    _vnc_server: RfbServer | None = field(init=False, repr=False, default=None)
    _vnc_dir: str | None = field(init=False, repr=False, default=None)

    def __post_init__(self):
        self._shared_device = NanoKVMUSBDevice(
            serial_port=self.serial_port,
            baud_rate=self.baud_rate,
            video_device=self.video_device,
            video_width=self.video_width,
            video_height=self.video_height,
            video_fps=self.video_fps,
            video_format=self.video_format,
            video_jpeg_quality=self.video_jpeg_quality,
            video_discard_stale=self.video_discard_stale,
            v4l2_ctl_executable=self.v4l2_ctl_executable,
            screen_width=self.screen_width,
            screen_height=self.screen_height,
        )
        self.children = {
            "video": NanoKVMUSBVideo(
                device=self._shared_device,
                serial_port=self.serial_port,
                baud_rate=self.baud_rate,
                video_device=self.video_device,
                video_width=self.video_width,
                video_height=self.video_height,
                video_fps=self.video_fps,
                video_format=self.video_format,
                video_jpeg_quality=self.video_jpeg_quality,
                video_discard_stale=self.video_discard_stale,
                video_stream_buffer_size=self.video_stream_buffer_size,
                screen_width=self.screen_width,
                screen_height=self.screen_height,
            ),
            "hid": NanoKVMUSBHID(
                device=self._shared_device,
                serial_port=self.serial_port,
                baud_rate=self.baud_rate,
                video_device=None,
                screen_width=self.screen_width,
                screen_height=self.screen_height,
            ),
        }
        if self.vnc_enabled:
            self._vnc_dir = tempfile.mkdtemp(prefix="nanokvm-usb-vnc-")
            vnc_path = str(Path(self._vnc_dir) / "vnc.sock")
            self._vnc_server = RfbServer(
                vnc_path,
                pump=lambda: self._shared_device._pump,
                hid=self._shared_device,
                width=self.video_width,
                height=self.video_height,
                password=self.vnc_password or None,
                tcp_port=self.vnc_tcp_port,
                tcp_bind=self.vnc_tcp_bind,
                layout=self.vnc_layout,
                on_client=self._shared_device.ensure_connected,
            )
            self.children["vnc"] = NanoKVMUSBVNC(
                path=vnc_path,
                default_encrypt=self.vnc_encrypt,
            )
        for name in ("video", "hid"):
            self.children[name]._owns_device = False

        super().__post_init__()
        if self._vnc_server is not None:
            if self.vnc_tcp_port is not None and not is_loopback_bind(self.vnc_tcp_bind) and not self.vnc_password:
                self.logger.warning(
                    "RFB TCP bind %s:%s has no vnc_password; the session is reachable on the network",
                    self.vnc_tcp_bind,
                    self.vnc_tcp_port,
                )
            self._vnc_server.start()

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_nanokvm_usb.client.NanoKVMUSBClient"

    def close(self):
        if self._vnc_server is not None:
            try:
                self._vnc_server.stop()
            except Exception as exc:
                self.logger.debug(f"Error stopping RFB server: {exc}")
            self._vnc_server = None
        try:
            super().close()
        except Exception as exc:
            self.logger.debug(f"Error closing child drivers: {exc}")
        if self._vnc_dir is not None:
            shutil.rmtree(self._vnc_dir, ignore_errors=True)
            self._vnc_dir = None
        try:
            self._shared_device.close()
        except Exception as exc:
            self.logger.debug(f"Error closing shared device: {exc}")
