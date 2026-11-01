"""Appium on the exporter for the lease's simulator."""

from __future__ import annotations

import socket
import threading
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jumpstarter_driver_appium.driver import AppiumDriver, validate_appium_config

from jumpstarter.common.exceptions import ConfigurationError

if TYPE_CHECKING:
    from .driver import IosSimulator


def validate_appium(config: Any) -> None:
    validate_appium_config(config, frozenset({"wda"}))
    if config is None:
        return
    wda = config.get("wda")
    if not isinstance(wda, str) or not wda.endswith(".app"):
        raise ConfigurationError("appium.wda must be the path of a prebuilt simulator WebDriverAgent .app")


def _wildcard_guards() -> tuple[int, list[socket.socket]]:
    """Reserve a port on both wildcard addresses without listening.

    WDA's MJPEG server ignores its binding IP and listens on every interface.
    Holding non-listening wildcard binds makes that bind fail, which WDA
    tolerates, so the simulator's screen is not broadcast to the network.
    Loopback binds would still succeed under macOS SO_REUSEADDR semantics.
    """
    guards = []
    try:
        ipv4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        guards.append(ipv4)
        ipv4.bind(("0.0.0.0", 0))
        port = ipv4.getsockname()[1]
        ipv6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        guards.append(ipv6)
        ipv6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        ipv6.bind(("::", port))
        return port, guards
    except BaseException:
        for guard in guards:
            guard.close()
        raise


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


@dataclass(kw_only=True)
class IosSimulatorAppium(AppiumDriver):
    """Appium for the lease's simulator, which must be powered on.

    Sessions are pinned to the lease's simulator, private device set and the
    operator's prebuilt WebDriverAgent, whose HTTP server stays on loopback.
    """

    parent: IosSimulator
    wda: str
    drivers: list[str] = field(default_factory=lambda: ["xcuitest"])

    def __post_init__(self):
        super().__post_init__()
        self._guards: list[socket.socket] = []
        self._guard_lock = threading.Lock()

    def prepare(self) -> None:
        info = self.parent._info()
        if not info["present"]:
            raise RuntimeError(info["reason"] or "The simulator is not running; power it on first")
        if not Path(self.wda).expanduser().is_dir():
            raise RuntimeError("The configured WebDriverAgent app is unavailable on the exporter")

    def state_root(self) -> str | None:
        # Inside the simulator's lease, so orphan cleanup also stops this Appium.
        return str(self.parent._lease_directory())

    def fixed_capabilities(self) -> dict[str, Any]:
        udid, platform_version, device_set = self.parent._appium_target()
        return {
            **self.capabilities,
            "platformName": "iOS",
            "appium:automationName": "XCUITest",
            "appium:udid": udid,
            "appium:platformVersion": platform_version,
            "appium:simulatorDevicesSetPath": device_set,
            "appium:usePreinstalledWDA": True,
            "appium:prebuiltWDAPath": str(Path(self.wda).expanduser().resolve()),
            "appium:wdaBindingIP": "127.0.0.1",
            # Erasing or shutting down simulators would break the lease's idb companion.
            "appium:fullReset": False,
            "appium:shutdownOtherSimulators": False,
            "appium:enforceFreshSimulatorCreation": False,
            # A headless launch would stop an existing Simulator UI process.
            "appium:isHeadless": False,
        }

    def session_capabilities(self) -> dict[str, Any]:
        port, guards = _wildcard_guards()
        with self._guard_lock:
            self._release_guards()
            self._guards = guards
        # Fresh ports avoid adopting unrelated listeners after an earlier session.
        return {"appium:wdaLocalPort": _free_loopback_port(), "appium:mjpegServerPort": port}

    def _release_guards(self) -> None:
        for guard in self._guards:
            with suppress(OSError):
                guard.close()
        self._guards = []

    def stop_server(self) -> None:
        super().stop_server()
        with self._guard_lock:
            self._release_guards()
