from base64 import b64encode
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import wraps
from io import BytesIO

import anyio
from aiohttp import ClientResponseError
from jumpstarter_driver_composite.driver import Composite
from nanokvm.client import NanoKVMClient as NanoKVMAPIClient
from nanokvm.models import MouseButton

from jumpstarter.driver import Driver, export, exportstream

# Re-export MouseButton for convenience
__all__ = ["NanoKVMVideo", "NanoKVMHID", "NanoKVM", "MouseButton"]


def _is_unauthorized_error(error: Exception) -> bool:
    """Check if an error is a 401 Unauthorized error"""
    if isinstance(error, ClientResponseError):
        return error.status == 401
    # Also check for string representation in case error is wrapped
    error_str = str(error)
    return "401" in error_str and ("Unauthorized" in error_str or "unauthorized" in error_str.lower())


def with_reauth(func):
    """Decorator to automatically re-authenticate on 401 errors"""
    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        try:
            return await func(self, *args, **kwargs)
        except Exception as e:
            if _is_unauthorized_error(e):
                self.logger.warning("Received 401 Unauthorized, re-authenticating...")
                await self._reset_client()
                # Retry once after re-authentication
                return await func(self, *args, **kwargs)
            raise
    return wrapper


def _format_info(info):
    """Format device info into a dictionary"""
    return {
        "ips": [
            {"name": ip.name, "addr": ip.addr, "version": ip.version, "type": ip.type}
            for ip in info.ips
        ],
        "mdns": info.mdns,
        "image": info.image,
        "application": info.application,
        "device_key": info.device_key,
    }


@dataclass(kw_only=True)
class NanoKVMDriverBase(Driver):
    """Base class for NanoKVM drivers with shared client management"""

    host: str
    username: str = "admin"
    password: str = "admin"

    _client: NanoKVMAPIClient | None = field(init=False, repr=False, default=None)
    _client_ctx: object = field(init=False, repr=False, default=None)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

    async def _reset_client(self):
        """Reset the client, forcing re-authentication"""
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as e:
                self.logger.debug(f"Error closing client during reset: {e}")
        self._client = None
        self._client_ctx = None

    async def _get_client(self) -> NanoKVMAPIClient:
        """Get or create the NanoKVM API client using context manager"""
        if self._client is None:
            self._client_ctx = NanoKVMAPIClient(f"http://{self.host}/api/")
            self._client = await self._client_ctx.__aenter__()
            await self._client.authenticate(self.username, self.password)
        return self._client

    def close(self):
        """Clean up resources"""
        if self._client_ctx is not None:
            try:
                anyio.from_thread.run(self._client_ctx.__aexit__(None, None, None))
            except Exception as e:
                self.logger.debug(f"Error closing client: {e}")


@dataclass(kw_only=True)
class NanoKVMVideo(NanoKVMDriverBase):
    """NanoKVM Video Streaming driver"""

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_nanokvm.client.NanoKVMVideoClient"

    @export
    @with_reauth
    async def snapshot(self, skip_frames: int = 3) -> str:
        """
        Take a snapshot from the video stream

        Returns:
            Base64 encoded JPEG image data
        """
        client = await self._get_client()
        frame_count = 0
        async for frame in client.mjpeg_stream():
            frame_count += 1
            # Skip the first frames as they're normally stale
            if frame_count < skip_frames:
                continue
            buffer = BytesIO()
            frame.save(buffer, format="JPEG")
            data = buffer.getvalue()
            self.logger.debug(f"snapshot: {len(data)} bytes")
            return b64encode(data).decode("ascii")
        raise RuntimeError("No frames available from video stream")

    @exportstream
    @asynccontextmanager
    async def stream(self):
        """
        Stream video frames as JPEG images

        Yields a stream that provides JPEG image data
        """
        self.logger.debug("Starting video stream")
        client = await self._get_client()

        send_stream, receive_stream = anyio.create_memory_object_stream(max_buffer_size=10)

        async def stream_video():
            async with send_stream:
                try:
                    async for frame in client.mjpeg_stream():
                        buffer = BytesIO()
                        frame.save(buffer, format="JPEG")
                        await send_stream.send(buffer.getvalue())
                except Exception as e:
                    if _is_unauthorized_error(e):
                        self.logger.warning("Received 401 Unauthorized during stream, re-authenticating...")
                        await self._reset_client()
                        new_client = await self._get_client()
                        async for frame in new_client.mjpeg_stream():
                            buffer = BytesIO()
                            frame.save(buffer, format="JPEG")
                            await send_stream.send(buffer.getvalue())
                    else:
                        self.logger.error(f"Error streaming video: {e}")
                        raise

        async with anyio.create_task_group() as tg:
            tg.start_soon(stream_video)
            try:
                yield receive_stream
            finally:
                tg.cancel_scope.cancel()


@dataclass(kw_only=True)
class NanoKVMHID(NanoKVMDriverBase):
    """NanoKVM HID (Keyboard/Mouse) driver"""

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_nanokvm.client.NanoKVMHIDClient"

    @export
    @with_reauth
    async def paste_text(self, text: str):
        """
        Paste text via keyboard HID simulation

        Args:
            text: Text to paste (limited character set supported)
        """
        client = await self._get_client()
        await client.paste_text(text)
        self.logger.info(f"Pasted text: {text}")

    @export
    @with_reauth
    async def press_key(self, key: str):
        """
        Press a key by pasting a single character

        Args:
            key: Single character or escape sequence to press (e.g., 'a', 'A', '\\n', '\\t')

        Note:
            This uses paste_text under the hood, so it supports the same character set.
            For special keys like Enter, use '\\n'. For Tab, use '\\t'.
        """
        if len(key) > 2:  # Allow for escape sequences like \n, \t
            self.logger.warning(f"press_key should be used with single characters, got: {key}")

        client = await self._get_client()
        await client.paste_text(key)
        self.logger.debug(f"Pressed key: {repr(key)}")

    @export
    @with_reauth
    async def reset_hid(self):
        """Reset the HID subsystem"""
        client = await self._get_client()
        await client.reset_hid()
        self.logger.info("HID subsystem reset")

    @export
    @with_reauth
    async def mouse_move_abs(self, x: float, y: float):
        """
        Move mouse to absolute coordinates

        Args:
            x: X coordinate (0.0 to 1.0, where 0.0 is left/top and 1.0 is right/bottom)
            y: Y coordinate (0.0 to 1.0, where 0.0 is left/top and 1.0 is right/bottom)
        """
        client = await self._get_client()
        await client.mouse_move_abs(x, y)
        self.logger.debug(f"Mouse moved to absolute position: ({x}, {y})")

    @export
    @with_reauth
    async def mouse_move_rel(self, dx: float, dy: float):
        """
        Move mouse relative to current position

        Args:
            dx: X movement delta (-1.0 to 1.0, where 1.0 is full screen width)
            dy: Y movement delta (-1.0 to 1.0, where 1.0 is full screen height)
        """
        client = await self._get_client()
        await client.mouse_move_rel(dx, dy)
        self.logger.debug(f"Mouse moved by relative offset: ({dx}, {dy})")

    @export
    @with_reauth
    async def mouse_click(self, button: MouseButton | str = "left", x: float | None = None, y: float | None = None):
        """
        Click a mouse button at current position or specified coordinates

        Args:
            button: Mouse button to click (MouseButton enum or "left", "right", "middle" string)
            x: Optional X coordinate (0.0 to 1.0) for absolute positioning before click
            y: Optional Y coordinate (0.0 to 1.0) for absolute positioning before click
        """
        # Convert string to MouseButton enum for backward compatibility
        if isinstance(button, str):
            button_map = {
                "left": MouseButton.LEFT,
                "right": MouseButton.RIGHT,
                "middle": MouseButton.MIDDLE,
            }
            button = button_map.get(button.lower(), MouseButton.LEFT)

        client = await self._get_client()
        await client.mouse_click(button, x, y)
        self.logger.info(f"Mouse {button.name} clicked")

    @export
    @with_reauth
    async def mouse_scroll(self, dx: int, dy: int):
        """
        Scroll the mouse wheel

        Args:
            dx: Horizontal scroll amount
            dy: Vertical scroll amount (positive=up, negative=down)
        """
        client = await self._get_client()
        await client.mouse_scroll(dx, dy)
        self.logger.debug(f"Mouse scrolled: ({dx}, {dy})")


@dataclass(kw_only=True)
class NanoKVM(Composite):
    """
    Composite driver for NanoKVM devices

    This driver provides:
    - Video streaming via the 'video' child driver
    - HID (Keyboard/Mouse) control via the 'hid' child driver
    """

    host: str
    username: str = "admin"
    password: str = "admin"

    def __post_init__(self):
        self.children = {
            "video": NanoKVMVideo(
                host=self.host,
                username=self.username,
                password=self.password,
            ),
            "hid": NanoKVMHID(
                host=self.host,
                username=self.username,
                password=self.password,
            ),
        }

        super().__post_init__()

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_nanokvm.client.NanoKVMClient"

    async def _get_client(self) -> NanoKVMAPIClient:
        """Delegate client access to the video child driver"""
        return await self.children["video"]._get_client()

    async def _reset_client(self):
        """Delegate client reset to the video child driver"""
        await self.children["video"]._reset_client()

    @export
    @with_reauth
    async def get_info(self):
        """Get device information"""
        client = await self._get_client()
        info = await client.get_info()
        return _format_info(info)

    @export
    @with_reauth
    async def reboot(self):
        """Reboot the NanoKVM device"""
        client = await self._get_client()
        await client.reboot_system()
        self.logger.info("NanoKVM device rebooted")

    @export
    @with_reauth
    async def mount_image(self, file: str = "", cdrom: bool = False):
        """
        Mount an image file or unmount if file is empty string

        Args:
            file: Path to image file on the NanoKVM device, or empty string to unmount
            cdrom: Whether to mount as CD-ROM (True) or disk (False)
        """
        client = await self._get_client()
        if file:
            await client.mount_image(file=file, cdrom=cdrom)
            self.logger.info(f"Mounted image: {file} (cdrom={cdrom})")
        else:
            await client.mount_image(file=None, cdrom=False)
            self.logger.info("Unmounted image")

    @export
    @with_reauth
    async def download_image(self, url: str):
        """
        Start downloading an image from a URL

        Args:
            url: URL of the image to download

        Returns:
            Dictionary with download status information
        """
        client = await self._get_client()
        status = await client.download_image(url=url)
        self.logger.info(f"Started download from {url}")
        return {
            "status": status.status,
            "file": status.file,
            "percentage": status.percentage,
        }

    @export
    @with_reauth
    async def get_mounted_image(self):
        """
        Get information about mounted image

        Returns:
            String with mounted image file path, or None if no image mounted
        """
        client = await self._get_client()
        info = await client.get_mounted_image()
        return info.file

    @export
    @with_reauth
    async def get_cdrom_status(self):
        """
        Check if the mounted image is in CD-ROM mode

        Returns:
            Boolean indicating if CD-ROM mode is active (True=CD-ROM, False=disk)
        """
        client = await self._get_client()
        status = await client.get_cdrom_status()
        return bool(status.cdrom)

    @export
    @with_reauth
    async def is_image_download_enabled(self):
        """
        Check if the /data partition allows image downloads

        Returns:
            Boolean indicating if image downloads are enabled
        """
        client = await self._get_client()
        status = await client.is_image_download_enabled()
        return status.enabled

    @export
    @with_reauth
    async def get_image_download_status(self):
        """
        Get the status of an ongoing image download

        Returns:
            Dictionary with download status, file, and percentage complete
        """
        client = await self._get_client()
        status = await client.get_image_download_status()
        return {
            "status": status.status,
            "file": status.file,
            "percentage": status.percentage,
        }

    @export
    @with_reauth
    async def get_images(self):
        """
        Get the list of available image files

        Returns:
            List of image file paths available on the NanoKVM device
        """
        client = await self._get_client()
        images = await client.get_images()
        return images.files
