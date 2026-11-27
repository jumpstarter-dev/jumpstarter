"""Client of an Android device's HMI: the generic HMI client, and the Android extras.

Everything a HMI client does (``video()``, ``input()``, ``record()``, ``status()``, ``set_quality()``, the
recording, ``j <hmi> view``...) is :class:`~jumpstarter_driver_hmi.client.HmiClient`'s. This adds what only an
Android device has: keys that work without the stream, text, apps, the clipboard, rotation, the notification panels and
the encoder list, as methods and as commands of the same ``j <hmi> ...`` group.
"""

from __future__ import annotations

from dataclasses import dataclass

import click
from jumpstarter_driver_hmi.client import HmiClient


@dataclass(kw_only=True)
class AdbDeviceHmiClient(HmiClient):
    """The client of :class:`~jumpstarter_driver_adb.hmi.driver.AdbDeviceHmi`."""

    def list_encoders(self) -> list[dict]:
        """The device's video encoders as scrcpy's server lists them: ``{"codec", "name", "hardware", "alias_of"}``."""
        return self.call("list_encoders")

    def press_key(self, key: str | int, press: str = "short") -> None:
        """Press an Android key (``HOME``, ``BACK``, ``APP_SWITCH``, ``POWER``, ``VOLUME_UP``...); works without the
        stream."""
        self.call("press_key", key, press)

    def type_text(self, text: str) -> None:
        """Type text into the focused field."""
        self.call("type_text", text)

    def start_app(self, package: str, force_stop: bool = False) -> None:
        """Start an app by its package name (``force_stop``: stop it first; a leading ``?``: search by label): over the
        control channel."""
        self.call("start_app", package, force_stop)

    def set_clipboard(self, text: str, paste: bool = False) -> None:
        """Put text on the device's clipboard (and paste it into the focused field if ``paste``)."""
        self.call("set_clipboard", text, paste)

    def get_clipboard(self) -> str:
        """The device's clipboard text (empty when there is none)."""
        return self.call("get_clipboard")

    def rotate(self, rotation: int | str | None = None) -> None:
        """Rotate the display and keep it there: no argument toggles portrait and landscape, ``0``-``3`` is a
        quarter-turn count, ``"auto"`` gives the rotation back to the sensor. Works without the stream."""
        self.call("rotate", rotation)

    def panel(self, name: str) -> None:
        """Pull down the ``notifications`` or ``settings`` panel, or ``collapse`` what is open."""
        self.call("panel", name)

    def cli(self):  # noqa: C901 (the generic group, and a few short commands)
        """The generic HMI command group, with the Android commands added.

        ``key`` and ``text`` replace the generic ones: they work without a stream (a key is then ``input keyevent`` over
        adb).
        """
        client = self
        hmi = super().cli()

        @hmi.command("key")
        @click.argument("key")
        @click.option("--press", type=click.Choice(["short", "long", "down", "up"]), default="short")
        def key_command(key, press):
            """Press an Android key: HOME, BACK, APP_SWITCH, POWER, VOLUME_UP, ..."""
            client.press_key(key, press)

        @hmi.command("text")
        @click.argument("text")
        def text_command(text):
            """Type text into the focused field."""
            client.type_text(text)

        @hmi.command("rotate")
        @click.argument("rotation", required=False)
        def rotate_command(rotation):
            """Rotate the display (no argument: portrait <-> landscape; 0-3: a rotation; auto: the sensor decides)."""
            client.rotate(int(rotation) if rotation and rotation.isdigit() else rotation)

        @hmi.command("app")
        @click.argument("package")
        @click.option("--force-stop", is_flag=True, help="stop the app first")
        def app_command(package, force_stop):
            """Start an app by its package name (a leading ? searches by label)."""
            client.start_app(package, force_stop)

        @hmi.group("clipboard")
        def clipboard_group():
            """The device's clipboard."""

        @clipboard_group.command("get")
        def clipboard_get_command():
            """Print the device's clipboard."""
            click.echo(client.get_clipboard())

        @clipboard_group.command("set")
        @click.argument("text")
        @click.option("--paste", is_flag=True, help="paste it into the focused field too")
        def clipboard_set_command(text, paste):
            """Put text on the device's clipboard."""
            client.set_clipboard(text, paste)

        @hmi.command("panel")
        @click.argument("name", type=click.Choice(["notifications", "settings", "collapse"]))
        def panel_command(name):
            """Pull down the notification or settings panel, or collapse what is open."""
            client.panel(name)

        @hmi.command("encoders")
        def encoders_command():
            """List the device's video encoders (hardware ones are marked), as the server sees them."""
            for encoder in client.list_encoders():
                kind = "hw" if encoder["hardware"] else "sw"
                alias = f"  (alias for {encoder['alias_of']})" if encoder["alias_of"] else ""
                click.echo(f"{encoder['codec']:5} {encoder['name']:45} {kind}{alias}")

        return hmi
