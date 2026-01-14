import glob
import os
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Optional

from anyio import sleep
from anyio._backends._asyncio import StreamReaderWrapper, StreamWriterWrapper
from serial_asyncio import open_serial_connection

from ..driver import AsyncSerial
from jumpstarter.driver import Driver, exportstream

# Default glob pattern for NVIDIA Tegra On-Platform Operator devices
NV_DEVICE_PATTERN = "/dev/serial/by-id/usb-NVIDIA_Tegra_On-Platform_Operator_*-if01"


def _has_glob_chars(path: str) -> bool:
    """Check if path contains glob wildcard characters."""
    return any(c in path for c in ("*", "?", "["))


def _resolve_device(pattern: str, logger) -> str | None:
    """Resolve a device path or glob pattern to an actual device path.

    Returns None if no device found.
    """
    if _has_glob_chars(pattern):
        matches = sorted(glob.glob(pattern))
        if not matches:
            return None
        if len(matches) > 1:
            logger.warning("Multiple devices match pattern '%s': %s. Using first: %s", pattern, matches, matches[0])
        return matches[0]
    else:
        # Direct path - check if exists
        if os.path.exists(pattern):
            return pattern
        return None


@dataclass(kw_only=True)
class NVDemuxSerial(Driver):
    """Serial driver for NVIDIA TCU demultiplexed UART channels.

    This driver wraps the nv_tcu_demuxer tool to extract a specific demultiplexed
    UART channel (like CCPLEX) from a multiplexed serial device. It automatically
    handles device reconnection when the target device restarts.

    Args:
        demuxer_path: Path to the nv_tcu_demuxer binary
        device: Device path or glob pattern for auto-detection.
                Default: /dev/serial/by-id/usb-NVIDIA_Tegra_On-Platform_Operator_*-if01
        target: Target channel to extract (e.g., "CCPLEX: 0")
        chip: Chip type for demuxer (T234 for Orin, T264 for Thor)
        baudrate: Baud rate for the serial connection
        cps: Characters per second throttling (optional)
        timeout: Timeout waiting for demuxer to detect pts
        poll_interval: Interval to poll for device reappearance after disconnect
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
    _shutdown: threading.Event = field(init=False, default_factory=threading.Event)
    _lock: threading.Lock = field(init=False, default_factory=threading.Lock)
    _pts_path: Optional[str] = field(init=False, default=None)
    _process: Optional[subprocess.Popen] = field(init=False, default=None)
    _monitor_thread: Optional[threading.Thread] = field(init=False, default=None)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        # Start the monitor thread
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True, name="NVDemuxSerial-monitor")
        self._monitor_thread.start()

        # Wait for initial ready state (with timeout)
        if not self._ready.wait(timeout=self.timeout):
            self.logger.warning("Timeout waiting for demuxer to become ready during initialization")

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_pyserial.client.PySerialClient"

    def _monitor_loop(self):
        """Background thread that manages demuxer lifecycle and auto-recovery."""
        while not self._shutdown.is_set():
            try:
                self._run_demuxer_cycle()
            except Exception as e:
                self.logger.error("Error in demuxer monitor loop: %s", e)
                # Clear ready state on error
                with self._lock:
                    self._pts_path = None
                self._ready.clear()
                # Wait before retrying
                if self._shutdown.wait(timeout=self.poll_interval):
                    break

    def _wait_for_device(self) -> str | None:
        """Wait for device to appear. Returns resolved device path or None if shutdown."""
        while not self._shutdown.is_set():
            resolved_device = _resolve_device(self.device, self.logger)
            if resolved_device:
                self.logger.info("Found device: %s", resolved_device)
                return resolved_device
            self.logger.debug("Device not found, polling... (pattern: %s)", self.device)
            if self._shutdown.wait(timeout=self.poll_interval):
                return None
        return None

    def _start_demuxer_process(self, device: str) -> bool:
        """Start the demuxer process. Returns True on success."""
        cmd = [self.demuxer_path, "-m", self.chip, "-d", device]
        self.logger.info("Starting demuxer: %s", " ".join(cmd))

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
            )
            return True
        except (FileNotFoundError, PermissionError) as e:
            self.logger.error("Failed to start demuxer: %s", e)
            return False

    def _parse_pts_from_line(self, line: str) -> str | None:
        """Parse a line for pts path matching the target. Returns pts path or None."""
        if self.target not in line:
            return None

        parts = line.split("\t")
        if len(parts) < 2:
            return None

        # Find the pts path (starts with /dev/) that's paired with our target
        for i, part in enumerate(parts):
            if self.target in part:
                for j, other_part in enumerate(parts):
                    if i != j and other_part.startswith("/dev/"):
                        return other_part
        return None

    def _read_demuxer_output(self):
        """Read demuxer stdout and parse for pts path."""
        pts_found = False
        try:
            for line in iter(self._process.stdout.readline, ""):
                if self._shutdown.is_set():
                    break

                line = line.strip()
                if not line:
                    continue

                self.logger.debug("Demuxer output: %s", line)

                if not pts_found:
                    pts_path = self._parse_pts_from_line(line)
                    if pts_path:
                        self.logger.info("Found pts path for target '%s': %s", self.target, pts_path)
                        with self._lock:
                            self._pts_path = pts_path
                        self._ready.set()
                        pts_found = True
        except Exception as e:
            self.logger.error("Error reading demuxer output: %s", e)

    def _cleanup_demuxer_process(self):
        """Clean up the demuxer process and clear ready state."""
        if self._process:
            try:
                self._process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()

            exit_code = self._process.returncode
            self.logger.info("Demuxer process exited with code %s", exit_code)
            self._process = None

        with self._lock:
            self._pts_path = None
        self._ready.clear()

    def _run_demuxer_cycle(self):
        """Run one cycle of: find device -> start demuxer -> monitor until exit."""
        resolved_device = self._wait_for_device()
        if not resolved_device or self._shutdown.is_set():
            return

        if not self._start_demuxer_process(resolved_device):
            self._shutdown.wait(timeout=self.poll_interval)
            return

        self._read_demuxer_output()
        self._cleanup_demuxer_process()

        if not self._shutdown.is_set():
            self.logger.info("Device disconnected, will poll for reconnection...")

    def close(self):
        """Stop the demuxer and monitor thread."""
        self._shutdown.set()

        # Terminate demuxer process if running
        if self._process:
            self.logger.info("Terminating demuxer process...")
            try:
                self._process.terminate()
                self._process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            self._process = None

        # Wait for monitor thread to exit
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2.0)

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

        # Get the current pts path
        with self._lock:
            pts_path = self._pts_path

        if not pts_path:
            raise RuntimeError("Demuxer ready but no pts path available")

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
