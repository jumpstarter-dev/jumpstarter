import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Optional

from anyio import sleep
from anyio._backends._asyncio import StreamReaderWrapper, StreamWriterWrapper
from serial_asyncio import open_serial_connection

from ..driver import AsyncSerial
from .manager import DemuxerManager
from jumpstarter.driver import Driver, exportstream

# Default glob pattern for NVIDIA Tegra On-Platform Operator devices
NV_DEVICE_PATTERN = "/dev/serial/by-id/usb-NVIDIA_Tegra_On-Platform_Operator_*-if01"


@dataclass(kw_only=True)
class NVDemuxSerial(Driver):
    """Serial driver for NVIDIA TCU demultiplexed UART channels.

    This driver wraps the nv_tcu_demuxer tool to extract a specific demultiplexed
    UART channel (like CCPLEX) from a multiplexed serial device. Multiple driver
    instances can share the same demuxer process by specifying different targets.

    Args:
        demuxer_path: Path to the nv_tcu_demuxer binary
        device: Device path or glob pattern for auto-detection.
                Default: /dev/serial/by-id/usb-NVIDIA_Tegra_On-Platform_Operator_*-if01
        target: Target channel to extract (e.g., "CCPLEX: 0", "BPMP: 1")
        chip: Chip type for demuxer (T234 for Orin, T264 for Thor)
        baudrate: Baud rate for the serial connection
        cps: Characters per second throttling (optional)
        timeout: Timeout waiting for demuxer to detect pts
        poll_interval: Interval to poll for device reappearance after disconnect

    Note:
        Multiple instances can be created with different targets. All instances
        must use the same demuxer_path, device, and chip configuration.
    """

    demuxer_path: str
    device: str = field(default=NV_DEVICE_PATTERN)
    target: str = field(default="CCPLEX: 0")
    chip: str = field(default="T264")
    baudrate: int = field(default=115200)
    cps: Optional[float] = field(default=None)
    timeout: float = field(default=10.0)
    poll_interval: float = field(default=1.0)

    # Internal state (not init params)
    _ready: threading.Event = field(init=False, default_factory=threading.Event)
    _registered: bool = field(init=False, default=False)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        # Register with the DemuxerManager
        manager = DemuxerManager.get_instance()
        try:
            manager.register_driver(
                driver_id=str(self.uuid),
                demuxer_path=self.demuxer_path,
                device=self.device,
                chip=self.chip,
                target=self.target,
                callback=self._on_target_ready,
                poll_interval=self.poll_interval,
            )
            self._registered = True
        except ValueError as e:
            self.logger.error("Failed to register with DemuxerManager: %s", e)
            raise


    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_pyserial.client.PySerialClient"

    def _on_target_ready(self, target: str, pts_path: str):
        """Callback invoked by DemuxerManager when target becomes ready.

        Args:
            target: The target channel that became ready
            pts_path: The pts path for this target
        """
        self.logger.info("Target '%s' ready with pts path: %s", target, pts_path)
        self._ready.set()

    def close(self):
        """Unregister from the DemuxerManager."""
        if self._registered:
            manager = DemuxerManager.get_instance()
            manager.unregister_driver(str(self.uuid))
            self._registered = False

        super().close()

    @exportstream
    @asynccontextmanager
    async def connect(self):
        """Connect to the demultiplexed serial port.

        Waits for the demuxer to be ready (device connected and pts path discovered)
        before opening the serial connection.
        """
        # Wait for ready state
        start_time = time.monotonic()
        while not self._ready.is_set():
            elapsed = time.monotonic() - start_time
            if elapsed >= self.timeout:
                raise TimeoutError(
                    f"Timeout waiting for demuxer to become ready (device pattern: {self.device})"
                )
            # Use a short sleep to allow checking ready state
            await sleep(0.1)

        # Get the current pts path from manager (retry until timeout)
        manager = DemuxerManager.get_instance()
        pts_start = time.monotonic()
        pts_path = manager.get_pts_path(str(self.uuid))
        while not pts_path:
            elapsed = time.monotonic() - pts_start
            if elapsed >= self.timeout:
                raise TimeoutError("Demuxer ready but no pts path available after retrying")
            await sleep(self.poll_interval)
            pts_path = manager.get_pts_path(str(self.uuid))

        cps_info = f", cps: {self.cps}" if self.cps is not None else ""
        self.logger.info("Connecting to %s, baudrate: %d%s", pts_path, self.baudrate, cps_info)

        reader, writer = await open_serial_connection(url=pts_path, baudrate=self.baudrate, limit=1)
        writer.transport.set_write_buffer_limits(high=4096, low=0)
        async with AsyncSerial(
            reader=StreamReaderWrapper(reader),
            writer=StreamWriterWrapper(writer),
            cps=self.cps,
        ) as stream:
            yield stream
        self.logger.info("Disconnected from %s", pts_path)
