"""Appium on the exporter for Android devices, through the exporter's ADB server."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from jumpstarter_driver_appium.driver import AppiumDriver

if TYPE_CHECKING:
    from .driver import AdbDevice

# UiAutomator2 capabilities that would start emulators with arbitrary arguments or
# send Appium's adb traffic to another host.
ANDROID_DENIED = frozenset({"avd", "avdArgs", "avdEnv", "remoteAdbHost"})


@dataclass(kw_only=True)
class AndroidAppium(AppiumDriver):
    """Appium with UiAutomator2 for one Android device on an exporter ADB server.

    Sessions are pinned to the device's serial and to the exporter's ADB server,
    which Appium is never allowed to kill: other drivers share it. Subclasses
    provide ``android_serial()`` and ``server_port()``.
    """

    drivers: list[str] = field(default_factory=lambda: ["uiautomator2"])

    def __post_init__(self):
        super().__post_init__()
        self._serial: str | None = None

    def android_serial(self) -> str:
        """The device's ADB serial on the exporter's server; raise if it is unavailable."""
        raise NotImplementedError

    def server_port(self) -> int:
        """The exporter ADB server's port."""
        raise NotImplementedError

    def prepare(self) -> None:
        self._serial = self.android_serial()

    def target_key(self) -> str | None:
        return self._serial

    def fixed_capabilities(self) -> dict[str, Any]:
        if self._serial is None:
            raise RuntimeError("Appium has not been started for this device; call info() first")
        return {
            **self.capabilities,
            "platformName": "Android",
            "appium:automationName": "UiAutomator2",
            "appium:udid": self._serial,
            "appium:adbPort": self.server_port(),
            "appium:suppressKillServer": True,
        }

    def denied_capabilities(self) -> frozenset[str]:
        return ANDROID_DENIED

    def server_environment(self) -> dict[str, str]:
        return {"ANDROID_ADB_SERVER_PORT": str(self.server_port()), **self.env}


@dataclass(kw_only=True)
class AdbDeviceAppium(AndroidAppium):
    """Appium for a declared Android device; its serial is resolved when Appium starts."""

    device: AdbDevice = field(repr=False)

    def server_port(self) -> int:
        return self.device.server_port

    def android_serial(self) -> str:
        # Takes a reference to the shared server, as every adb user here must.
        self.device.adb_env()
        if self.device.transport == "tcp":
            host, port = self.device._resolve_endpoint()
            return f"{host}:{port}"
        info = self.device.info()
        if info.get("present") != "yes":
            raise RuntimeError(info.get("reason") or "The Android device is not present")
        serial = info["serial"]
        # A configured serial is reported present without asking adb, so check that the
        # server sees it: Appium must not keep a session on a device that has left.
        states = {visible: state for visible, state, _ in self.device._visible_devices()}
        if states.get(serial) != "device":
            state = states.get(serial)
            raise RuntimeError(f"The Android device {serial} is {state or 'not connected'}")
        return serial
