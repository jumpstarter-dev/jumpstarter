import io
from base64 import b64decode
from dataclasses import dataclass

import click
from jumpstarter_driver_composite.client import CompositeClient
from PIL import Image

from jumpstarter.client import DriverClient
from jumpstarter.client.decorators import driver_click_group


@dataclass(kw_only=True)
class NanoKVMVideoClient(DriverClient):
    """
    Client interface for NanoKVM video streaming

    This client provides methods to access video stream and snapshots
    from the NanoKVM device.
    """

    def snapshot(self, skip_frames: int = 3) -> Image.Image:
        """
        Get a snapshot image from the video stream

        Returns:
            PIL Image object of the snapshot
        """
        input_jpg_data = b64decode(self.call("snapshot", skip_frames))
        return Image.open(io.BytesIO(input_jpg_data))

    def cli(self):
        @driver_click_group(self)
        def base():
            """NanoKVM Video commands"""
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
class NanoKVMHIDClient(DriverClient):
    """
    Client interface for NanoKVM HID (Keyboard/Mouse) control

    This client provides methods to send keyboard and mouse events
    to the device connected to the NanoKVM.
    """

    def paste_text(self, text: str):
        """
        Paste text via keyboard HID simulation

        Args:
            text: Text to paste. Supports limited character set:
                  alphanumeric, basic punctuation, and common symbols

        Example::

            hid.paste_text("Hello, World!")
            hid.paste_text("ls -la\\n")
        """
        self.call("paste_text", text)

    def press_key(self, key: str):
        """
        Press a key by pasting a single character

        Args:
            key: Single character or escape sequence to press

        Example::

            hid.press_key("a")      # Type 'a'
            hid.press_key("A")      # Type 'A'
            hid.press_key("\\n")    # Press Enter
            hid.press_key("\\t")    # Press Tab
        """
        self.call("press_key", key)

    def reset_hid(self):
        """
        Reset the HID subsystem

        This can help recover from HID communication issues.
        """
        self.call("reset_hid")

    def mouse_move_abs(self, x: float, y: float):
        """
        Move mouse to absolute coordinates

        Args:
            x: X coordinate (0.0 to 1.0, where 0.0 is left and 1.0 is right)
            y: Y coordinate (0.0 to 1.0, where 0.0 is top and 1.0 is bottom)

        Example::

            # Move to center of screen
            hid.mouse_move_abs(0.5, 0.5)

            # Move to top-left corner
            hid.mouse_move_abs(0.0, 0.0)

            # Move to bottom-right corner
            hid.mouse_move_abs(1.0, 1.0)
        """
        self.call("mouse_move_abs", x, y)

    def mouse_move_rel(self, dx: float, dy: float):
        """
        Move mouse relative to current position

        Args:
            dx: X movement delta (-1.0 to 1.0, where 1.0 is full screen width)
            dy: Y movement delta (-1.0 to 1.0, where 1.0 is full screen height)

        Example::

            # Move right by 10% of screen width and down by 10%
            hid.mouse_move_rel(0.1, 0.1)

            # Move left by 20%
            hid.mouse_move_rel(-0.2, 0.0)
        """
        self.call("mouse_move_rel", dx, dy)

    def mouse_click(self, button: str = "left", x: float | None = None, y: float | None = None):
        """
        Click a mouse button

        Args:
            button: Mouse button to click ("left", "right", "middle")
            x: Optional X coordinate (0.0 to 1.0) for absolute positioning before click
            y: Optional Y coordinate (0.0 to 1.0) for absolute positioning before click

        Example::

            # Click at current position
            hid.mouse_click("left")

            # Click at center of screen
            hid.mouse_click("left", 0.5, 0.5)

            # Right-click at specific location
            hid.mouse_click("right", 0.75, 0.25)
        """
        if x is not None and y is not None:
            self.call("mouse_click", button, x, y)
        else:
            self.call("mouse_click", button, None, None)

    def mouse_scroll(self, dx: int, dy: int):
        """
        Scroll the mouse wheel

        Args:
            dx: Horizontal scroll amount
            dy: Vertical scroll amount (positive=up, negative=down)

        Example::

            # Scroll up
            hid.mouse_scroll(0, 5)

            # Scroll down
            hid.mouse_scroll(0, -5)
        """
        self.call("mouse_scroll", dx, dy)

    def cli(self):  # noqa: C901
        @driver_click_group(self)
        def base():
            """NanoKVM HID (Keyboard/Mouse) commands"""
            pass

        @base.command()
        @click.argument("text")
        def paste(text):
            """Paste text via keyboard HID (supports \\n for newline, \\t for tab)"""
            # Decode escape sequences like \n, \t, etc.
            decoded_text = text.encode().decode("unicode_escape")
            self.paste_text(decoded_text)
            click.echo(f"Pasted: {repr(decoded_text)}")

        @base.command()
        @click.argument("key")
        def press(key):
            """Press a single key (supports \\n for Enter, \\t for Tab)"""
            # Decode escape sequences
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
            """Move mouse by relative offset (-1.0 to 1.0, where 1.0 is full screen)"""
            self.mouse_move_rel(dx, dy)
            click.echo(f"Mouse moved by ({dx}, {dy})")

        @mouse.command(name="click")
        @click.option("--button", "-b", default="left", type=click.Choice(["left", "right", "middle"]))
        @click.option("--x", type=float, default=None, help="Optional X coordinate (0.0-1.0)")
        @click.option("--y", type=float, default=None, help="Optional Y coordinate (0.0-1.0)")
        def mouse_click_cmd(button, x, y):
            """Click a mouse button"""
            self.mouse_click(button, x, y)
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
class NanoKVMClient(CompositeClient):
    """
    Client interface for NanoKVM devices

    This composite client provides access to all NanoKVM functionality:
    - video: Video streaming and snapshots
    - hid: Keyboard and mouse control
    - serial: Serial console access (if enabled)

    Example::

        # Get a snapshot
        image = nanokvm.video.snapshot()

        # Paste text
        nanokvm.hid.paste_text("Hello from Jumpstarter!")

        # Get device info
        info = nanokvm.get_info()
        print(f"Device: {info['mdns']}")
    """

    def get_info(self) -> dict:
        """
        Get device information

        Returns:
            Dictionary containing device information:
            - ips: List of IP addresses
            - mdns: mDNS hostname
            - image: Image version
            - application: Application version
            - device_key: Device key
        """
        return self.call("get_info")

    def reboot(self):
        """
        Reboot the NanoKVM device

        Warning:
            This will reboot the NanoKVM itself, not the connected device.
            The connection will be lost during reboot.
        """
        self.call("reboot")

    def cli(self):
        """Create CLI interface with device management and child commands"""
        base = super().cli()

        @base.command()
        def info():
            """Get device information"""
            info = self.get_info()
            click.echo("NanoKVM Device Information:")
            click.echo(f"  mDNS: {info['mdns']}")
            click.echo(f"  Image version: {info['image']}")
            click.echo(f"  Application version: {info['application']}")
            click.echo(f"  Device key: {info['device_key']}")
            if info['ips']:
                click.echo("  IP Addresses:")
                for ip in info['ips']:
                    click.echo(f"    - {ip['name']}: {ip['addr']} ({ip['type']}, {ip['version']})")

        @base.command()
        @click.confirmation_option(prompt="Are you sure you want to reboot the NanoKVM device?")
        def reboot():
            """Reboot the NanoKVM device"""
            self.reboot()
            click.echo("NanoKVM device is rebooting...")

        return base
