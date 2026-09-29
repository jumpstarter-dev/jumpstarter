from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

from .common import CaptureConfig, CaptureResult
from jumpstarter.client import DriverClient


@dataclass(kw_only=True)
class SigrokClient(DriverClient):
    """Client methods for the Sigrok driver."""

    def scan(self) -> str:
        return self.call("scan")

    def capture(self, config: CaptureConfig | dict) -> CaptureResult:
        return CaptureResult.model_validate(self.call("capture", config))

    def capture_stream(self, config: CaptureConfig | dict) -> Generator[bytes, None, None]:
        """Stream capture data from sigrok-cli.

        Args:
            config: CaptureConfig or dict with capture parameters

        Yields:
            bytes: Chunks of captured data
        """
        yield from self.streamingcall("capture_stream", config)  # pragma: no cover

    def get_driver_info(self) -> dict[str, Any]:
        return self.call("get_driver_info")

    def get_channel_map(self) -> dict[str, str]:
        return self.call("get_channel_map")

    def list_output_formats(self) -> list[str]:
        return self.call("list_output_formats")

