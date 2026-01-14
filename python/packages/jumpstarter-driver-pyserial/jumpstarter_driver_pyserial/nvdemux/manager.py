"""Singleton manager for NVIDIA TCU demuxer process.

Manages a single shared demuxer process that can be accessed by multiple
NVDemuxSerial driver instances. Handles process lifecycle, device reconnection,
and distributes pts paths to registered drivers.
"""

import glob
import logging
import os
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def _has_glob_chars(path: str) -> bool:
    """Check if path contains glob wildcard characters."""
    return any(c in path for c in ("*", "?", "["))


def _resolve_device(pattern: str) -> str | None:
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


@dataclass
class DriverInfo:
    """Information about a registered driver."""

    driver_id: str
    target: str
    callback: Callable[[str, str], None]  # (target, pts_path) -> None


class DemuxerManager:
    """Singleton manager for the NVIDIA TCU demuxer process.

    Manages a single shared demuxer process and distributes pts paths to
    multiple driver instances based on their target channels.
    """

    _instance: Optional["DemuxerManager"] = None
    _instance_lock = threading.Lock()

    def __init__(self):
        """Private constructor. Use get_instance() instead."""
        self._lock = threading.Lock()
        self._drivers: dict[str, DriverInfo] = {}
        self._pts_map: dict[str, str] = {}  # target -> pts_path
        self._ready_targets: set[str] = set()
        self._process: Optional[subprocess.Popen] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._shutdown = threading.Event()

        # Process configuration (must be same for all drivers)
        self._demuxer_path: Optional[str] = None
        self._device: Optional[str] = None
        self._chip: Optional[str] = None
        self._poll_interval: float = 1.0

    @classmethod
    def get_instance(cls) -> "DemuxerManager":
        """Get the singleton instance of DemuxerManager."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """Reset the singleton instance. Used for testing."""
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance._cleanup()
            cls._instance = None

    def _validate_config(self, demuxer_path: str, device: str, chip: str, target: str):
        """Validate driver configuration against existing drivers.

        Raises:
            ValueError: If configuration doesn't match or target is duplicate
        """
        if self._demuxer_path != demuxer_path:
            raise ValueError(f"Demuxer path mismatch: existing={self._demuxer_path}, new={demuxer_path}")
        if self._device != device:
            raise ValueError(f"Device mismatch: existing={self._device}, new={device}")
        if self._chip != chip:
            raise ValueError(f"Chip mismatch: existing={self._chip}, new={chip}")

        # Check for duplicate target
        for existing_driver in self._drivers.values():
            if existing_driver.target == target:
                raise ValueError(f"Target '{target}' already registered by another driver")

    def _notify_if_ready(self, target: str, callback: Callable[[str, str], None]):
        """Notify driver immediately if target is already ready."""
        if target in self._ready_targets:
            pts_path = self._pts_map.get(target)
            if pts_path:
                try:
                    callback(target, pts_path)
                except Exception as e:
                    logger.error("Error in driver callback: %s", e)

    def register_driver(
        self,
        driver_id: str,
        demuxer_path: str,
        device: str,
        chip: str,
        target: str,
        callback: Callable[[str, str], None],
        poll_interval: float = 1.0,
    ) -> None:
        """Register a driver instance with the manager.

        Args:
            driver_id: Unique identifier for the driver
            demuxer_path: Path to nv_tcu_demuxer binary
            device: Device path or glob pattern
            chip: Chip type (T234 or T264)
            target: Target channel (e.g., "CCPLEX: 0")
            callback: Function to call when target becomes ready
            poll_interval: Polling interval for device reconnection

        Raises:
            ValueError: If configuration doesn't match existing process
        """
        with self._lock:
            # Validate configuration matches existing process
            if self._drivers:
                self._validate_config(demuxer_path, device, chip, target)
            else:
                # First driver - set process configuration
                self._demuxer_path = demuxer_path
                self._device = device
                self._chip = chip
                self._poll_interval = poll_interval

            # Register the driver
            driver_info = DriverInfo(driver_id=driver_id, target=target, callback=callback)
            self._drivers[driver_id] = driver_info

            logger.info("Registered driver %s for target '%s'", driver_id, target)

            # If target is already ready, notify immediately
            self._notify_if_ready(target, callback)

            # Start monitor thread if this is the first driver
            if len(self._drivers) == 1:
                self._start_monitor()

    def unregister_driver(self, driver_id: str) -> None:
        """Unregister a driver instance.

        Args:
            driver_id: Unique identifier for the driver
        """
        with self._lock:
            if driver_id in self._drivers:
                target = self._drivers[driver_id].target
                del self._drivers[driver_id]
                logger.info("Unregistered driver %s (target: %s)", driver_id, target)

                # Stop monitor thread if this was the last driver
                if not self._drivers:
                    self._stop_monitor()

    def get_pts_path(self, driver_id: str) -> str | None:
        """Get the pts path for a registered driver.

        Args:
            driver_id: Unique identifier for the driver

        Returns:
            The pts path or None if not available
        """
        with self._lock:
            if driver_id not in self._drivers:
                return None
            target = self._drivers[driver_id].target
            return self._pts_map.get(target)

    def is_ready(self, target: str) -> bool:
        """Check if a target is ready.

        Args:
            target: Target channel to check

        Returns:
            True if the target is ready
        """
        with self._lock:
            return target in self._ready_targets

    def _start_monitor(self):
        """Start the monitor thread."""
        self._shutdown.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="DemuxerManager-monitor"
        )
        self._monitor_thread.start()
        logger.info("Started demuxer monitor thread")

    def _stop_monitor(self):
        """Stop the monitor thread."""
        self._shutdown.set()

        # Terminate process if running
        if self._process:
            logger.info("Terminating demuxer process...")
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
        self._monitor_thread = None

        logger.info("Stopped demuxer monitor thread")

    def _cleanup(self):
        """Clean up resources."""
        self._stop_monitor()
        self._drivers.clear()
        self._pts_map.clear()
        self._ready_targets.clear()

    def _monitor_loop(self):
        """Background thread that manages demuxer lifecycle and auto-recovery."""
        while not self._shutdown.is_set():
            try:
                self._run_demuxer_cycle()
            except Exception as e:
                logger.error("Error in demuxer monitor loop: %s", e)
                # Clear ready state on error
                with self._lock:
                    self._pts_map.clear()
                    self._ready_targets.clear()
                # Wait before retrying
                if self._shutdown.wait(timeout=self._poll_interval):
                    break

    def _run_demuxer_cycle(self):
        """Run one cycle of: find device -> start demuxer -> monitor until exit."""
        # Wait for device to appear
        resolved_device = self._wait_for_device()
        if not resolved_device or self._shutdown.is_set():
            return

        # Start demuxer process
        if not self._start_demuxer_process(resolved_device):
            self._shutdown.wait(timeout=self._poll_interval)
            return

        # Read and parse demuxer output
        self._read_demuxer_output()

        # Cleanup process
        self._cleanup_demuxer_process()

        if not self._shutdown.is_set():
            logger.info("Device disconnected, will poll for reconnection...")

    def _wait_for_device(self) -> str | None:
        """Wait for device to appear. Returns resolved device path or None if shutdown."""
        while not self._shutdown.is_set():
            resolved_device = _resolve_device(self._device)
            if resolved_device:
                logger.info("Found device: %s", resolved_device)
                return resolved_device
            logger.debug("Device not found, polling... (pattern: %s)", self._device)
            if self._shutdown.wait(timeout=self._poll_interval):
                return None
        return None

    def _start_demuxer_process(self, device: str) -> bool:
        """Start the demuxer process. Returns True on success."""
        cmd = [self._demuxer_path, "-m", self._chip, "-d", device]
        logger.info("Starting demuxer: %s", " ".join(cmd))

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
            logger.error("Failed to start demuxer: %s", e)
            return False

    def _parse_demuxer_line(self, line: str) -> tuple[str | None, str | None]:
        """Parse a demuxer output line to extract pts path and target.

        Returns:
            Tuple of (pts_path, target) or (None, None) if not found
        """
        parts = line.split("\t")
        if len(parts) < 2:
            return None, None

        pts_path = None
        target = None

        for part in parts:
            if part.startswith("/dev/"):
                pts_path = part
            elif ":" in part:  # Targets have format "NAME: N"
                target = part

        return pts_path, target

    def _read_demuxer_output(self):
        """Read demuxer stdout and parse all pts paths."""
        try:
            for line in iter(self._process.stdout.readline, ""):
                if self._shutdown.is_set():
                    break

                line = line.strip()
                if not line:
                    continue

                logger.debug("Demuxer output: %s", line)

                # Parse line format: "<pts_path>\t<target>"
                pts_path, target = self._parse_demuxer_line(line)

                if pts_path and target:
                    logger.info("Found pts path for target '%s': %s", target, pts_path)

                    # Update state immediately for this target
                    with self._lock:
                        self._pts_map[target] = pts_path
                        self._ready_targets.add(target)

                        # Notify driver for this specific target
                        for driver_id, driver_info in self._drivers.items():
                            if driver_info.target == target:
                                try:
                                    driver_info.callback(target, pts_path)
                                except Exception as e:
                                    logger.error("Error in driver %s callback: %s", driver_id, e)
                                break  # Only one driver per target

        except Exception as e:
            logger.error("Error reading demuxer output: %s", e)

        # Clear state when process ends
        with self._lock:
            self._pts_map.clear()
            self._ready_targets.clear()

    def _cleanup_demuxer_process(self):
        """Clean up the demuxer process and clear ready state."""
        if self._process:
            try:
                self._process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()

            exit_code = self._process.returncode
            logger.info("Demuxer process exited with code %s", exit_code)
            self._process = None

        with self._lock:
            self._pts_map.clear()
            self._ready_targets.clear()
