"""Singleton manager for NVIDIA TCU demuxer process.

Manages a single shared demuxer process that can be accessed by multiple
NVDemuxSerial driver instances. Handles process lifecycle, device reconnection,
and distributes pts paths to registered drivers.
"""

import atexit
import ctypes
import glob
import logging
import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Platform detection
_IS_LINUX = sys.platform.startswith("linux")


def _get_preexec_fn() -> Callable[[], None] | None:
    """Get platform-specific preexec_fn for subprocess.

    On Linux, returns a function that sets PR_SET_PDEATHSIG to SIGTERM,
    ensuring the subprocess receives SIGTERM when the parent process dies.
    This works even if the parent is killed with SIGKILL.

    On other platforms, returns None.
    """
    if not _IS_LINUX:
        return None

    def set_pdeathsig():
        """Set parent death signal to SIGTERM via prctl."""
        try:
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            PR_SET_PDEATHSIG = 1
            result = libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
            if result != 0:
                errno = ctypes.get_errno()
                logger.warning("prctl(PR_SET_PDEATHSIG) failed with errno %d", errno)
        except Exception as e:
            logger.warning("Failed to set parent death signal: %s", e)

    return set_pdeathsig


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


class DemuxerManager:
    """Singleton manager for the NVIDIA TCU demuxer process.

    Manages a single shared demuxer process and distributes pts paths to
    multiple driver instances based on their target channels.
    """

    _instance: Optional["DemuxerManager"] = None
    _instance_lock = threading.Lock()
    _signal_handlers_installed = False
    _original_sigterm_handler: signal.Handlers | None = None
    _original_sigint_handler: signal.Handlers | None = None

    def __init__(self):
        """Private constructor. Use get_instance() instead."""
        self._lock = threading.Lock()
        self._drivers: dict[str, DriverInfo] = {}
        self._pts_map: dict[str, str] = {}  # target -> pts_path
        self._ready_targets: set[str] = set()
        self._process: Optional[subprocess.Popen] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._shutdown = threading.Event()
        self._cleanup_done = False

        # Process configuration (must be same for all drivers)
        self._demuxer_path: Optional[str] = None
        self._device: Optional[str] = None
        self._chip: Optional[str] = None
        self._poll_interval: float = 1.0

        # Register atexit handler for cleanup on normal exit
        atexit.register(self._atexit_cleanup)
        logger.debug("Registered atexit handler for demuxer cleanup")

        # Install signal handlers (only once globally)
        self._install_signal_handlers()

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
                # Reset cleanup_done flag before cleanup to allow cleanup to run
                cls._instance._cleanup_done = False
                cls._instance._cleanup()
            cls._instance = None

    def _atexit_cleanup(self):
        """Cleanup handler called on normal program exit via atexit."""
        if self._cleanup_done:
            return
        logger.debug("atexit cleanup triggered")
        self._cleanup()

    def _install_signal_handlers(self):
        """Install signal handlers for SIGTERM and SIGINT.

        Handlers ensure cleanup is performed before the process terminates.
        Only installs handlers once globally, and preserves original handlers.
        """
        cls = type(self)
        if cls._signal_handlers_installed:
            return

        def make_handler(sig: signal.Signals) -> Callable[[int, any], None]:
            """Create a signal handler that cleans up and re-raises the signal."""

            def handler(signum: int, frame):
                logger.debug("Signal %s received, cleaning up demuxer process", sig.name)
                # Cleanup the demuxer process
                if cls._instance is not None:
                    cls._instance._cleanup()

                # Restore original handler and re-raise signal
                if sig == signal.SIGTERM and cls._original_sigterm_handler is not None:
                    signal.signal(signal.SIGTERM, cls._original_sigterm_handler)
                elif sig == signal.SIGINT and cls._original_sigint_handler is not None:
                    signal.signal(signal.SIGINT, cls._original_sigint_handler)

                # Re-raise the signal to allow normal termination
                os.kill(os.getpid(), signum)

            return handler

        try:
            # Only install signal handlers from the main thread
            if threading.current_thread() is not threading.main_thread():
                logger.debug("Not installing signal handlers from non-main thread")
                return

            cls._original_sigterm_handler = signal.signal(signal.SIGTERM, make_handler(signal.SIGTERM))
            cls._original_sigint_handler = signal.signal(signal.SIGINT, make_handler(signal.SIGINT))
            cls._signal_handlers_installed = True
            logger.debug("Installed signal handlers for SIGTERM and SIGINT")
        except Exception as e:
            logger.warning("Failed to install signal handlers: %s", e)

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

    def register_driver(
        self,
        driver_id: str,
        demuxer_path: str,
        device: str,
        chip: str,
        target: str,
        poll_interval: float = 1.0,
    ) -> None:
        """Register a driver instance with the manager.

        Args:
            driver_id: Unique identifier for the driver
            demuxer_path: Path to nv_tcu_demuxer binary
            device: Device path or glob pattern
            chip: Chip type (T234 or T264)
            target: Target channel (e.g., "CCPLEX: 0")
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
            driver_info = DriverInfo(driver_id=driver_id, target=target)
            self._drivers[driver_id] = driver_info

            logger.debug("Registered driver %s for target '%s'", driver_id, target)

            # Start monitor thread only once
            if not self._monitor_thread or not self._monitor_thread.is_alive():
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
                logger.debug("Unregistered driver %s (target: %s)", driver_id, target)

                # Keep monitor running even if no drivers remain

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
        logger.debug("Started demuxer monitor thread")

    def _stop_monitor(self):
        """Stop the monitor thread.

        This method is idempotent - safe to call multiple times.
        """
        self._shutdown.set()

        # Terminate process if running
        process = self._process
        if process is not None:
            logger.debug("Terminating demuxer process (PID %s)...", process.pid)
            try:
                # First try graceful termination
                process.terminate()
                try:
                    process.wait(timeout=5.0)
                    logger.debug("Demuxer process terminated gracefully")
                except subprocess.TimeoutExpired:
                    # Force kill if it doesn't respond
                    logger.warning("Demuxer process did not terminate, killing...")
                    process.kill()
                    process.wait(timeout=2.0)
                    logger.debug("Demuxer process killed")
            except ProcessLookupError:
                # Process already dead
                logger.debug("Demuxer process already exited")
            except Exception as e:
                logger.error("Error terminating demuxer process: %s", e)
            finally:
                self._process = None

        # Wait for monitor thread to exit
        monitor_thread = self._monitor_thread
        if monitor_thread is not None and monitor_thread.is_alive():
            # Don't join if we're being called from the monitor thread itself
            if threading.current_thread() is not monitor_thread:
                monitor_thread.join(timeout=2.0)
                if monitor_thread.is_alive():
                    logger.warning("Monitor thread did not exit within timeout")
        self._monitor_thread = None

        logger.debug("Stopped demuxer monitor")

    def _cleanup(self):
        """Clean up resources.

        This method is idempotent - safe to call multiple times.
        Ensures the demuxer process is terminated on program exit.
        """
        if self._cleanup_done:
            logger.debug("Cleanup already done, skipping")
            return

        logger.debug("Cleaning up DemuxerManager resources")
        self._cleanup_done = True

        self._stop_monitor()

        with self._lock:
            self._drivers.clear()
            self._pts_map.clear()
            self._ready_targets.clear()

        logger.info("DemuxerManager cleanup complete")

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
            # Always wait for poll interval before retrying
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

        # Read and parse demuxer output (stdout and stderr concurrently)
        stderr_thread = threading.Thread(target=self._read_demuxer_stderr, daemon=True)
        stderr_thread.start()

        self._read_demuxer_output()

        # Wait for stderr thread to finish
        stderr_thread.join(timeout=1.0)

        # Cleanup process
        self._cleanup_demuxer_process()

        if not self._shutdown.is_set():
            logger.info("Device disconnected, will poll for reconnection...")

    def _wait_for_device(self) -> str | None:
        """Wait for device to appear. Returns resolved device path or None if shutdown."""
        while not self._shutdown.is_set():
            resolved_device = _resolve_device(self._device)
            if resolved_device:
                logger.debug("Found device: %s", resolved_device)
                return resolved_device
            logger.debug("Device not found, polling... (pattern: %s)", self._device)
            if self._shutdown.wait(timeout=self._poll_interval):
                return None
        return None

    def _start_demuxer_process(self, device: str) -> bool:
        """Start the demuxer process. Returns True on success.

        On Linux, uses prctl(PR_SET_PDEATHSIG) to ensure the subprocess
        receives SIGTERM when the parent dies (including kill -9).
        """
        cmd = [self._demuxer_path, "-m", self._chip, "-d", device]
        logger.debug("Starting demuxer: %s", " ".join(cmd))

        # Get platform-specific preexec_fn (Linux: set parent death signal)
        preexec_fn = _get_preexec_fn()

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
                preexec_fn=preexec_fn,
            )
            logger.debug("Demuxer process started with PID %d", self._process.pid)
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

        # First part is the pts path, second part is the target name
        pts_path = parts[0].strip() if parts[0].startswith("/dev/") else None
        target = parts[1].strip() if len(parts) >= 2 else None

        return pts_path, target

    def _read_demuxer_stderr(self):
        """Read demuxer stderr and check for catastrophic errors."""
        try:
            for line in iter(self._process.stderr.readline, ""):
                if self._shutdown.is_set():
                    break

                line = line.strip()
                if not line:
                    continue

                logger.warning("Demuxer stderr: %s", line)

                # Check for catastrophic file lock error
                if "ERROR: unable to obtain file lock" in line:
                    logger.critical(
                        "Demuxer file lock error detected. Another instance may be running. "
                        "Terminating exporter to prevent conflicts."
                    )
                    # Force immediate process termination
                    os._exit(1)

        except Exception as e:
            logger.error("Error reading demuxer stderr: %s", e)

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
                    logger.debug("Found pts path for target '%s': %s", target, pts_path)

                    with self._lock:
                        self._pts_map[target] = pts_path
                        self._ready_targets.add(target)

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
