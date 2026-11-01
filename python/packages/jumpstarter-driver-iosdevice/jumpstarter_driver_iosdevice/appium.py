"""Appium on the exporter for the leased physical device."""

from __future__ import annotations

import asyncio
import threading
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from anyio import from_thread
from jumpstarter_driver_appium.driver import AppiumDriver, validate_appium_config

from jumpstarter.common.exceptions import ConfigurationError

if TYPE_CHECKING:
    from .driver import IosDevice

CHUNK_SIZE = 65536


def validate_appium(config: Any) -> None:
    validate_appium_config(config, frozenset({"wda_port"}))
    if config is None:
        return
    port = config.get("wda_port")
    if port is not None and (type(port) is not int or not 1 <= port <= 65535):
        raise ConfigurationError("appium.wda_port must be a device port between 1 and 65535")


class _WdaForward:
    """Forward an exporter loopback port to a port on the device, on its own event loop.

    Appium reaches an already running WebDriverAgent through ``webDriverAgentUrl``,
    which must be a TCP address on the exporter. Each connection opens a fresh
    usbmux connection to the device port.
    """

    def __init__(self, device: IosDevice, udid: str, port: int):
        self.device, self.udid, self.port = device, udid, port
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="appium-wda-forward", daemon=True)
        self.thread.start()
        self.server = asyncio.run_coroutine_threadsafe(
            asyncio.start_server(self._handle, "127.0.0.1", 0), self.loop
        ).result(timeout=10)
        self.local_port = self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async with self.device._daemon().connect_device(self.udid, self.port) as upstream:

                async def to_device():
                    while data := await reader.read(CHUNK_SIZE):
                        await upstream.send(data)

                async def from_device():
                    with suppress(Exception):
                        while True:
                            writer.write(await upstream.receive(CHUNK_SIZE))
                            await writer.drain()

                tasks = [asyncio.ensure_future(to_device()), asyncio.ensure_future(from_device())]
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in tasks:
                    task.cancel()
        except Exception as exc:  # noqa: BLE001 - one failed connection must not stop the forward
            self.device.logger.warning("WebDriverAgent forward failed: %s", exc)
        finally:
            writer.close()

    def close(self) -> None:
        async def stop():
            self.server.close()
            await self.server.wait_closed()

        with suppress(Exception):
            asyncio.run_coroutine_threadsafe(stop(), self.loop).result(timeout=10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=10)
        self.loop.close()


@dataclass(kw_only=True)
class IosDeviceAppium(AppiumDriver):
    """Appium for the leased device, through the exporter's own usbmux daemon.

    Sessions are pinned to the device's udid and iOS version. With ``wda_port``,
    Appium attaches to a WebDriverAgent already running on the device; otherwise
    the operator's ``capabilities`` choose how Appium starts it, for example
    ``appium:usePreinstalledWDA`` with ``appium:updatedWDABundleId``.
    """

    device: IosDevice = field(repr=False)
    wda_port: int | None = None
    drivers: list[str] = field(default_factory=lambda: ["xcuitest"])

    def __post_init__(self):
        super().__post_init__()
        self._target: tuple[str, str] | None = None
        self._forward: _WdaForward | None = None
        self._forward_lock = threading.Lock()

    def prepare(self) -> None:
        info = from_thread.run(self.device.info)
        if not info["present"]:
            raise RuntimeError(info["reason"] or "The iOS device is not present")
        self._target = (info["udid"], info["os_version"])
        if self.wda_port is not None:
            with self._forward_lock:
                if self._forward is not None and self._forward.udid != info["udid"]:
                    self._forward.close()
                    self._forward = None
                if self._forward is None:
                    self._forward = _WdaForward(self.device, info["udid"], self.wda_port)

    def fixed_capabilities(self) -> dict[str, Any]:
        if self._target is None:
            raise RuntimeError("Appium has not been started for this device; call info() first")
        udid, platform_version = self._target
        fixed = {
            **self.capabilities,
            "platformName": "iOS",
            "appium:automationName": "XCUITest",
            "appium:udid": udid,
            "appium:platformVersion": platform_version,
        }
        with self._forward_lock:
            if self._forward is not None:
                fixed["appium:webDriverAgentUrl"] = f"http://127.0.0.1:{self._forward.local_port}"
        return fixed

    def server_environment(self) -> dict[str, str]:
        if self.device.transport == "network":
            address = f"{self.device.usbmux_host}:{self.device.usbmux_port}"
        else:
            address = f"UNIX:{self.device.usbmux_socket}"
        return {"USBMUXD_SOCKET_ADDRESS": address, **self.env}

    def stop_server(self) -> None:
        super().stop_server()
        with self._forward_lock:
            forward, self._forward = self._forward, None
        if forward is not None:
            forward.close()
