import io
from base64 import b64decode
from contextlib import contextmanager
from dataclasses import dataclass

import click
from jumpstarter_driver_composite.client import CompositeClient
from PIL import Image

from jumpstarter.client import DriverClient
from jumpstarter.client.decorators import driver_click_group

from .mouse import MouseButton

__all__ = ["NanoKVMUSBVideoClient", "NanoKVMUSBHIDClient", "NanoKVMUSBClient", "MouseButton"]


@dataclass(kw_only=True)
class NanoKVMUSBVideoClient(DriverClient):
    """Client interface for NanoKVM-USB video capture."""

    def snapshot(self, skip_frames: int = 3) -> Image.Image:
        input_jpg_data = b64decode(self.call("snapshot", skip_frames))
        return Image.open(io.BytesIO(input_jpg_data))

    @contextmanager
    def stream(self, method: str = "stream"):
        """
        Open a live JPEG frame stream tunneled through Jumpstarter.

        Each ``receive()`` returns one JPEG frame as raw bytes. For recording,
        OCR, and preprocessing use ``edge-clearance-delivery/video-receiver/`` with
        ``stream-bridge.py`` so ``jmp shell`` stays interactive.
        """
        with super().stream(method) as stream:
            yield stream

    def cli(self):
        @driver_click_group(self)
        def base():
            """NanoKVM-USB video commands"""
            pass

        @base.command()
        @click.argument("output", type=click.Path(), default="snapshot.jpg")
        def snapshot(output):
            """Take a snapshot and save to file"""
            image = self.snapshot()
            image.save(output)
            click.echo(f"Snapshot saved to {output}")
            click.echo(f"Image size: {image.size[0]}x{image.size[1]}")

        return base


@dataclass(kw_only=True)
class NanoKVMUSBHIDClient(DriverClient):
    """Client interface for NanoKVM-USB HID control."""

    def paste_text(self, text: str):
        self.call("paste_text", text)

    def press_key(self, key: str):
        self.call("press_key", key)

    def reset_hid(self):
        self.call("reset_hid")

    def mouse_move_abs(self, x: float, y: float):
        self.call("mouse_move_abs", x, y)

    def mouse_move_rel(self, dx: float, dy: float):
        self.call("mouse_move_rel", dx, dy)

    def mouse_click(self, button: MouseButton | str = "left", x: float | None = None, y: float | None = None):
        if x is not None and y is not None:
            self.call("mouse_click", button, x, y)
        else:
            self.call("mouse_click", button, None, None)

    def mouse_scroll(self, dx: int, dy: int):
        self.call("mouse_scroll", dx, dy)

    def cli(self):  # noqa: C901
        @driver_click_group(self)
        def base():
            """NanoKVM-USB HID commands"""
            pass

        @base.command()
        @click.argument("text")
        def paste(text):
            """Paste text via keyboard HID (supports \\n for newline, \\t for tab)"""
            decoded_text = text.encode().decode("unicode_escape")
            self.paste_text(decoded_text)
            click.echo(f"Pasted: {repr(decoded_text)}")

        @base.command()
        @click.argument("key")
        def press(key):
            """Press a single key (supports \\n for Enter, \\t for Tab)"""
            decoded_key = key.encode().decode("unicode_escape")
            self.press_key(decoded_key)
            click.echo(f"Pressed: {repr(decoded_key)}")

        @base.command()
        def reset():
            """Reset the HID subsystem"""
            self.reset_hid()
            click.echo("HID subsystem reset")

        @base.group()
        def mouse():
            """Mouse control commands"""
            pass

        @mouse.command()
        @click.argument("x", type=float)
        @click.argument("y", type=float)
        def move(x, y):
            """Move mouse to absolute coordinates (0.0-1.0)"""
            self.mouse_move_abs(x, y)
            click.echo(f"Mouse moved to ({x}, {y})")

        @mouse.command()
        @click.argument("dx", type=float)
        @click.argument("dy", type=float)
        def move_rel(dx, dy):
            """Move mouse by relative offset (-1.0 to 1.0)"""
            self.mouse_move_rel(dx, dy)
            click.echo(f"Mouse moved by ({dx}, {dy})")

        @mouse.command(name="click")
        @click.option("--button", "-b", default="left", type=click.Choice(["left", "right", "middle"]))
        @click.option("--x", type=float, default=None, help="Optional X coordinate (0.0-1.0)")
        @click.option("--y", type=float, default=None, help="Optional Y coordinate (0.0-1.0)")
        def mouse_click_cmd(button, x, y):
            """Click a mouse button"""
            button_map = {
                "left": MouseButton.LEFT,
                "right": MouseButton.RIGHT,
                "middle": MouseButton.MIDDLE,
            }
            self.mouse_click(button_map[button], x, y)
            if x is not None and y is not None:
                click.echo(f"Clicked {button} button at ({x}, {y})")
            else:
                click.echo(f"Clicked {button} button")

        @mouse.command()
        @click.option("--dx", type=int, default=0, help="Horizontal scroll")
        @click.option("--dy", type=int, default=-5, help="Vertical scroll")
        def scroll(dx, dy):
            """Scroll the mouse wheel"""
            self.mouse_scroll(dx, dy)
            click.echo(f"Scrolled ({dx}, {dy})")

        return base


@dataclass(kw_only=True)
class NanoKVMUSBClient(CompositeClient):
    """
    Client interface for NanoKVM-USB devices.

    Provides access to:
    - video: UVC snapshot capture and live stream
    - hid: keyboard and mouse control over USB serial
    """

    def cli(self):
        return super().cli()
