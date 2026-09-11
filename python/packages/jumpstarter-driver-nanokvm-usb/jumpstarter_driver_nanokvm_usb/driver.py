from base64 import b64encode
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import anyio
from anyio import to_thread
from jumpstarter_driver_composite.driver import Composite

from jumpstarter.driver import Driver, export, exportstream

from .device import NanoKVMUSBDevice
from .keyboard import resolve_key_code
from .mouse import MouseButton

__all__ = ["NanoKVMUSBVideo", "NanoKVMUSBHID", "NanoKVMUSB", "MouseButton"]


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
                screen_width=self.screen_width,
                screen_height=self.screen_height,
            )
            self._owns_device = True
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

    async def _ensure_device(self) -> NanoKVMUSBDevice:
        assert self.device is not None
        if not self.device.is_connected:
            await to_thread.run_sync(self.device.connect)
        return self.device

    def close(self):
        if self._owns_device and self.device is not None:
            try:
                anyio.from_thread.run(self.device.close)
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
            if not device.is_connected:
                device.connect()
            for _ in range(skip_frames):
                device.capture_frame_jpeg()
            data = device.capture_frame_jpeg()
            return data

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
            frame_interval = 1.0 / self.video_fps if self.video_fps > 0 else 0.0
            async with send_stream:
                while True:
                    data = await to_thread.run_sync(
                        self.device.capture_frame_jpeg,  # type: ignore[union-attr]
                        self.video_jpeg_quality,
                    )
                    try:
                        await send_stream.send(data)
                    except anyio.BrokenResourceError:
                        break
                    if frame_interval > 0:
                        await anyio.sleep(frame_interval)

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
        self.logger.info(f"Pasted text: {text}")

    @export
    async def press_key(self, key: str):
        if len(key) > 2:
            try:
                resolve_key_code(key)
            except ValueError:
                self.logger.warning(
                    f"press_key should be used with single characters, got: {key}"
                )

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
        if isinstance(button, str):
            button_map = {
                "left": MouseButton.LEFT,
                "right": MouseButton.RIGHT,
                "middle": MouseButton.MIDDLE,
            }
            button = button_map.get(button.lower(), MouseButton.LEFT)

        device = await self._ensure_device()
        await to_thread.run_sync(device.mouse_click, button, x, y)
        self.logger.info(f"Mouse {button.name} clicked")

    @export
    async def mouse_scroll(self, dx: int, dy: int):
        device = await self._ensure_device()
        await to_thread.run_sync(device.mouse_scroll, dx, dy)
        self.logger.debug(f"Mouse scrolled: ({dx}, {dy})")


@dataclass(kw_only=True)
class NanoKVMUSB(Composite):
    """
    Composite driver for NanoKVM-USB devices.

    Provides video capture and HID control over USB serial + UVC.
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
    screen_width: int = 1920
    screen_height: int = 1080

    _shared_device: NanoKVMUSBDevice = field(init=False, repr=False)

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
        for child in self.children.values():
            child._owns_device = False

        super().__post_init__()

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_nanokvm_usb.client.NanoKVMUSBClient"

    def close(self):
        try:
            anyio.from_thread.run(self._shared_device.close)
        except Exception as exc:
            self.logger.debug(f"Error closing shared device: {exc}")
