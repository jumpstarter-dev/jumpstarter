from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from collections.abc import AsyncGenerator
from dataclasses import field
from typing import Optional

from pydantic import ConfigDict, validate_call
from pydantic.dataclasses import dataclass

from .common import (
    GptpOffset,
    GptpParentInfo,
    GptpPortStats,
    GptpStatus,
    GptpSyncEvent,
    PortState,
    ServoState,
)
from jumpstarter.driver import Driver, export

logger = logging.getLogger(__name__)

_VALID_PROFILES = {"gptp", "default"}
_VALID_TRANSPORTS = {"L2", "UDPv4", "UDPv6"}
_VALID_ROLES = {"master", "slave", "auto"}
_INTERFACE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,14}$")
_DENIED_PTP4L_ARGS = frozenset({
    "-f", "--config", "-i", "--interface",
    "--uds_address", "--log_file",
})

_OFFSET_RE = re.compile(
    r"ptp4l\[[\d.]+\]:\s+(?:master\s+)?offset\s+(-?\d+)\s+(\w+)\s+freq\s+([+-]?\d+)\s+path\s+delay\s+(-?\d+)"
)
_PORT_STATE_RE = re.compile(
    r"ptp4l\[[\d.]+\]:\s+port\s+\d+(?:\s*\([^)]*\))?:\s+(\w+)\s+to\s+(\w+)\s+on\s+(\w+)"
)


class ParsedLogLine:
    """Result of parsing a single ptp4l log line."""

    def __init__(self):
        self.offset_ns: Optional[float] = None
        self.freq_ppb: Optional[float] = None
        self.path_delay_ns: Optional[float] = None
        self.servo_state: Optional[str] = None
        self.port_state: Optional[str] = None
        self.event: Optional[str] = None


def parse_ptp4l_log_line(line: str) -> Optional[ParsedLogLine]:
    """Parse a single ptp4l log line into structured data.

    Extracts offset/frequency/delay from sync lines and port state
    transitions from state-change lines.

    Args:
        line: Raw log line from ptp4l stdout.

    Returns:
        Parsed result or None if the line is not recognized.
    """
    m = _OFFSET_RE.search(line)
    if m:
        result = ParsedLogLine()
        result.offset_ns = float(m.group(1))
        result.servo_state = m.group(2)
        result.freq_ppb = float(m.group(3))
        result.path_delay_ns = float(m.group(4))
        return result

    m = _PORT_STATE_RE.search(line)
    if m:
        result = ParsedLogLine()
        result.port_state = m.group(2)
        result.event = m.group(3)
        return result

    return None


def _generate_ptp4l_config(
    interface: str,
    domain: int,
    profile: str,
    transport: str,
    role: str,
    priority1: int = 128,
) -> str:
    """Generate ptp4l configuration file content.

    Args:
        interface: Network interface name for the [interface] section.
        domain: PTP domain number.
        profile: ``"gptp"`` or ``"default"``.
        transport: ``"L2"``, ``"UDPv4"``, or ``"UDPv6"``.
        role: ``"master"``, ``"slave"``, or ``"auto"``.
        priority1: Clock priority1 value (0-255).

    Returns:
        INI-style configuration string for ptp4l.
    """
    lines = ["[global]"]
    lines.append(f"domainNumber\t\t{domain}")
    lines.append(f"network_transport\t{transport}")

    if profile == "gptp":
        lines.append("transportSpecific\t0x1")
        lines.append("time_stamping\t\thardware")
        lines.append("follow_up_info\t\t1")
        lines.append("gmCapable\t\t1")

    if role == "slave":
        lines.append("slaveOnly\t\t1")
    elif role == "master":
        lines.append(f"priority1\t\t{priority1}")
        lines.append("priority2\t\t0")
    else:
        lines.append(f"priority1\t\t{priority1}")

    lines.append(f"\n[{interface}]")
    return "\n".join(lines) + "\n"


def _validate_extra_args(args: list[str]) -> None:
    """Reject ptp4l CLI arguments that could override safety-critical settings.

    Raises:
        ValueError: If a denied argument is found.
    """
    for arg in args:
        base = arg.split("=", 1)[0]
        if base in _DENIED_PTP4L_ARGS:
            raise ValueError(
                f"ptp4l_extra_args contains denied argument {arg!r}; "
                f"denied list: {sorted(_DENIED_PTP4L_ARGS)}"
            )


@dataclass(kw_only=True, config=ConfigDict(arbitrary_types_allowed=True))
class Gptp(Driver):
    """gPTP/PTP driver managing linuxptp (ptp4l/phc2sys) for time synchronization.

    Provides lifecycle management, status monitoring, and configuration of
    IEEE 802.1AS (gPTP) or IEEE 1588 (PTPv2) time synchronization between
    the exporter host and a target device.

    Attributes:
        interface: Network interface name (e.g. ``eth0``).
        domain: PTP domain number (0-127).
        profile: ``"gptp"`` (IEEE 802.1AS) or ``"default"`` (IEEE 1588).
        transport: ``"L2"``, ``"UDPv4"``, or ``"UDPv6"``.
        role: ``"master"``, ``"slave"``, or ``"auto"`` (BMCA election).
        sync_system_clock: Whether to run ``phc2sys`` for CLOCK_REALTIME sync.
        ptp4l_extra_args: Additional trusted ptp4l CLI arguments.
    """

    interface: str
    domain: int = 0
    profile: str = "gptp"
    transport: str = "L2"
    role: str = "auto"
    sync_system_clock: bool = True
    ptp4l_extra_args: list[str] = field(default_factory=list)

    _ptp4l_proc: Optional[asyncio.subprocess.Process] = field(
        init=False, default=None, repr=False
    )
    _phc2sys_proc: Optional[asyncio.subprocess.Process] = field(
        init=False, default=None, repr=False
    )
    _config_file_path: Optional[str] = field(
        init=False, default=None, repr=False
    )
    _port_state: str = field(init=False, default="INITIALIZING")
    _servo_state: str = field(init=False, default="s0")
    _last_offset_ns: float = field(init=False, default=0.0)
    _last_path_delay_ns: float = field(init=False, default=0.0)
    _last_freq_ppb: float = field(init=False, default=0.0)
    _priority1: int = field(init=False, default=128)
    _stats: dict[str, int] = field(init=False, default_factory=dict)
    _reader_task: Optional[asyncio.Task] = field(
        init=False, default=None, repr=False
    )

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        if not _INTERFACE_RE.match(self.interface):
            raise ValueError(
                f"Invalid interface name: {self.interface!r}. "
                "Must match [a-zA-Z0-9][a-zA-Z0-9._-]{0,14}"
            )
        if self.profile not in _VALID_PROFILES:
            raise ValueError(
                f"Invalid profile: {self.profile!r}. Must be one of {_VALID_PROFILES}"
            )
        if self.transport not in _VALID_TRANSPORTS:
            raise ValueError(
                f"Invalid transport: {self.transport!r}. Must be one of {_VALID_TRANSPORTS}"
            )
        if self.role not in _VALID_ROLES:
            raise ValueError(
                f"Invalid role: {self.role!r}. Must be one of {_VALID_ROLES}"
            )
        _validate_extra_args(self.ptp4l_extra_args)

    @classmethod
    def client(cls) -> str:
        """Return the fully-qualified client class path."""
        return "jumpstarter_driver_gptp.client.GptpClient"

    async def _supports_hw_timestamping(self) -> bool:
        """Check if the interface supports hardware timestamping via ethtool.

        Runs ethtool asynchronously to avoid blocking the event loop.

        Returns:
            True if hardware-transmit and hardware-receive are supported.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "ethtool", "-T", self.interface,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            output = stdout.decode("utf-8", errors="replace")
            return "hardware-transmit" in output and "hardware-receive" in output
        except (FileNotFoundError, asyncio.TimeoutError, OSError):
            return False

    def _require_started(self) -> None:
        """Raise RuntimeError if ptp4l is not running.

        Checks both that the process handle exists and that the process
        has not exited.
        """
        if self._ptp4l_proc is None:
            raise RuntimeError("ptp4l not started -- call start() first")
        if self._ptp4l_proc.returncode is not None:
            self._ptp4l_proc = None
            self._synchronized_invalidate()
            raise RuntimeError("ptp4l process has exited unexpectedly")

    def _synchronized_invalidate(self) -> None:
        """Reset sync-related state when ptp4l dies or stops."""
        self._port_state = "INITIALIZING"
        self._servo_state = "s0"

    def _on_reader_done(self, task: asyncio.Task) -> None:
        """Log unhandled exceptions from the background reader task."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self.logger.error("ptp4l reader task failed: %s", exc)

    async def _read_ptp4l_output(self) -> None:
        """Background task: read ptp4l stdout and update internal state.

        On EOF (process exit), invalidates the session so subsequent
        calls to ``_require_started()`` will raise.
        """
        proc = self._ptp4l_proc
        if proc is None or proc.stdout is None:
            return
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                self._ptp4l_proc = None
                self._synchronized_invalidate()
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            self.logger.debug("ptp4l: %s", line)
            parsed = parse_ptp4l_log_line(line)
            if parsed is None:
                continue
            if parsed.offset_ns is not None:
                self._last_offset_ns = parsed.offset_ns
                self._last_freq_ppb = parsed.freq_ppb or 0.0
                self._last_path_delay_ns = parsed.path_delay_ns or 0.0
                if parsed.servo_state:
                    self._servo_state = parsed.servo_state
                self._stats["sync_count"] = self._stats.get("sync_count", 0) + 1
            if parsed.port_state is not None:
                self._port_state = parsed.port_state

    async def _cleanup(self) -> None:
        """Clean up all resources: processes, reader task, config file.

        Safe to call even if partially initialized. Order:
        1. Terminate ptp4l and wait
        2. Cancel reader task
        3. Terminate phc2sys and wait
        4. Remove temp config file
        """
        if self._ptp4l_proc is not None:
            self._ptp4l_proc.terminate()
            try:
                await asyncio.wait_for(self._ptp4l_proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._ptp4l_proc.kill()
                await self._ptp4l_proc.wait()
            self._ptp4l_proc = None

        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self._phc2sys_proc is not None:
            self._phc2sys_proc.terminate()
            try:
                await asyncio.wait_for(self._phc2sys_proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._phc2sys_proc.kill()
                await self._phc2sys_proc.wait()
            self._phc2sys_proc = None

        if self._config_file_path is not None:
            try:
                os.unlink(self._config_file_path)
            except OSError:
                pass
            self._config_file_path = None

    @export
    async def start(self) -> None:
        """Start PTP synchronization by spawning ptp4l (and optionally phc2sys).

        Creates a temporary ptp4l config file, spawns the ptp4l process,
        and optionally spawns phc2sys for system clock synchronization.

        Raises:
            RuntimeError: If ptp4l is already running or exits immediately.
        """
        if self._ptp4l_proc is not None:
            raise RuntimeError("ptp4l already running")

        try:
            config_content = _generate_ptp4l_config(
                interface=self.interface,
                domain=self.domain,
                profile=self.profile,
                transport=self.transport,
                role=self.role,
                priority1=self._priority1,
            )

            fd = tempfile.mkstemp(suffix=".cfg", prefix="ptp4l_")
            os.fchmod(fd[0], 0o600)
            with os.fdopen(fd[0], "w") as f:
                f.write(config_content)
            self._config_file_path = fd[1]

            hw_ts = await self._supports_hw_timestamping()
            ts_flag = "-H" if hw_ts else "-S"
            if not hw_ts:
                self.logger.warning(
                    "Hardware timestamping not available on %s, falling back to software timestamping",
                    self.interface,
                )

            cmd = [
                "ptp4l",
                "-f", self._config_file_path,
                "-i", self.interface,
                ts_flag,
                "-m",
                *self.ptp4l_extra_args,
            ]
            self.logger.info("Starting ptp4l: %s", " ".join(cmd))
            self._ptp4l_proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )

            self._port_state = "INITIALIZING"
            self._servo_state = "s0"
            self._priority1 = 128
            self._stats = {}
            self._reader_task = asyncio.create_task(self._read_ptp4l_output())
            self._reader_task.add_done_callback(self._on_reader_done)

            await asyncio.sleep(0.5)
            if self._ptp4l_proc is not None and self._ptp4l_proc.returncode is not None:
                raise RuntimeError(
                    f"ptp4l exited immediately with code {self._ptp4l_proc.returncode}"
                )

            if self.sync_system_clock and hw_ts:
                phc2sys_cmd = [
                    "phc2sys",
                    "-s", self.interface,
                    "-c", "CLOCK_REALTIME",
                    "-w", "-m",
                ]
                self.logger.info("Starting phc2sys: %s", " ".join(phc2sys_cmd))
                self._phc2sys_proc = await asyncio.create_subprocess_exec(
                    *phc2sys_cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                )

                await asyncio.sleep(0.5)
                if self._phc2sys_proc.returncode is not None:
                    raise RuntimeError(
                        f"phc2sys exited immediately with code {self._phc2sys_proc.returncode}"
                    )
        except Exception:
            await self._cleanup()
            raise

    @export
    async def stop(self) -> None:
        """Stop PTP synchronization and clean up all resources.

        Terminates ptp4l and phc2sys processes, cancels the reader task,
        and removes the temporary config file.

        Raises:
            RuntimeError: If ptp4l is not started.
        """
        if self._ptp4l_proc is None and self._config_file_path is None:
            raise RuntimeError("ptp4l not started -- call start() first")

        await self._cleanup()
        self._synchronized_invalidate()

    @export
    @validate_call(validate_return=True)
    def status(self) -> GptpStatus:
        """Query the current PTP synchronization status.

        Returns:
            Current synchronization status including port state,
            offset, delay, and servo state.

        Raises:
            RuntimeError: If ptp4l is not started.
        """
        self._require_started()
        return GptpStatus(
            port_state=PortState(self._port_state),
            offset_ns=self._last_offset_ns,
            mean_delay_ns=self._last_path_delay_ns,
            servo_state=ServoState(self._servo_state),
        )

    @export
    @validate_call(validate_return=True)
    def get_offset(self) -> GptpOffset:
        """Get the current clock offset from master.

        Returns:
            Offset measurement including path delay and frequency.

        Raises:
            RuntimeError: If ptp4l is not started.
        """
        self._require_started()
        return GptpOffset(
            offset_from_master_ns=self._last_offset_ns,
            mean_path_delay_ns=self._last_path_delay_ns,
            freq_ppb=self._last_freq_ppb,
            timestamp=time.time(),
        )

    @export
    @validate_call(validate_return=True)
    def get_port_stats(self) -> GptpPortStats:
        """Get PTP port statistics.

        Returns:
            Port statistics counters (sync, followup, pdelay, announce).

        Raises:
            RuntimeError: If ptp4l is not started.
        """
        self._require_started()
        return GptpPortStats(
            sync_count=self._stats.get("sync_count", 0),
            followup_count=self._stats.get("followup_count", 0),
            pdelay_req_count=self._stats.get("pdelay_req_count", 0),
            pdelay_resp_count=self._stats.get("pdelay_resp_count", 0),
            announce_count=self._stats.get("announce_count", 0),
        )

    @export
    @validate_call(validate_return=True)
    def get_clock_identity(self) -> str:
        """Get this clock's identity string.

        Not yet implemented — requires ptp4l UDS management socket
        integration for structured TLV queries.

        Raises:
            NotImplementedError: Always, until UDS integration is added.
        """
        self._require_started()
        raise NotImplementedError(
            "get_clock_identity requires ptp4l UDS management socket integration"
        )

    @export
    @validate_call(validate_return=True)
    def get_parent_info(self) -> GptpParentInfo:
        """Get information about the parent/grandmaster clock.

        Not yet implemented — requires ptp4l UDS management socket
        integration for structured TLV queries.

        Raises:
            NotImplementedError: Always, until UDS integration is added.
        """
        self._require_started()
        raise NotImplementedError(
            "get_parent_info requires ptp4l UDS management socket integration"
        )

    @export
    @validate_call(validate_return=True)
    def set_priority1(self, priority: int) -> None:
        """Set clock priority1 to influence BMCA master election.

        Not yet implemented — requires ptp4l UDS management socket
        integration or config-reload mechanism.

        Args:
            priority: Priority1 value (0-255).

        Raises:
            NotImplementedError: Always, until UDS integration is added.
        """
        self._require_started()
        raise NotImplementedError(
            "set_priority1 requires ptp4l UDS management socket integration "
            "or config-reload mechanism"
        )

    @export
    @validate_call(validate_return=True)
    def is_synchronized(self) -> bool:
        """Check whether PTP is synchronized (servo locked in SLAVE state).

        Returns:
            True if the port is in SLAVE state and servo is locked (s2).

        Raises:
            RuntimeError: If ptp4l is not started.
        """
        self._require_started()
        return self._port_state == "SLAVE" and self._servo_state == "s2"

    @export
    async def read(self) -> AsyncGenerator[GptpSyncEvent, None]:
        """Stream periodic sync status updates.

        Yields ``GptpSyncEvent`` approximately once per second with current
        offset, delay, and state. Streams indefinitely until the session
        is cancelled or the process exits.

        Yields:
            Sync event with current offset, delay, state, and timestamp.

        Raises:
            RuntimeError: If ptp4l is not started.
        """
        self._require_started()
        prev_state = self._port_state
        while True:
            if self._ptp4l_proc is None or self._ptp4l_proc.returncode is not None:
                return

            event_type = "sync"
            if self._port_state != prev_state:
                event_type = "state_change"
                prev_state = self._port_state
            if self._port_state == "FAULTY":
                event_type = "fault"

            yield GptpSyncEvent(
                event_type=event_type,
                port_state=PortState(self._port_state) if self._port_state in PortState.__members__ else None,
                servo_state=ServoState(self._servo_state) if self._servo_state in ("s0", "s1", "s2") else None,
                offset_ns=self._last_offset_ns,
                path_delay_ns=self._last_path_delay_ns,
                freq_ppb=self._last_freq_ppb,
                timestamp=time.time(),
            )
            await asyncio.sleep(1.0)


class MockGptpBackend:
    """Default backend for MockGptp.

    Can be replaced with ``StatefulPtp4l`` for stateful testing.
    Tracks process lifecycle and simulated PTP state.
    """

    def __init__(self):
        self._started = False
        self._port_state = "INITIALIZING"
        self._servo_state = "s0"
        self._offset_ns = 0.0
        self._priority1 = 128

    def require_started(self):
        """Raise RuntimeError if the mock is not started."""
        if not self._started:
            raise RuntimeError("ptp4l not started -- call start() first")

    def start(self):
        """Start mock synchronization — immediately enters SLAVE/s2 state."""
        if self._started:
            raise RuntimeError("ptp4l already running")
        self._started = True
        self._port_state = "SLAVE"
        self._servo_state = "s2"
        self._offset_ns = -23.0
        self._priority1 = 128

    def stop(self):
        """Stop mock synchronization and reset state."""
        self.require_started()
        self._started = False
        self._port_state = "INITIALIZING"
        self._servo_state = "s0"
        self._offset_ns = 0.0

    def set_priority1(self, priority: int):
        """Set priority1 and simulate BMCA role change."""
        self.require_started()
        self._priority1 = priority
        if priority < 128 and self._port_state in ("SLAVE", "LISTENING", "PASSIVE"):
            self._port_state = "MASTER"


@dataclass(kw_only=True, config=ConfigDict(arbitrary_types_allowed=True))
class MockGptp(Driver):
    """Mock gPTP driver for testing without real PTP hardware.

    Simulates PTP synchronization behavior: after ``start()``, immediately
    enters SLAVE state with a small simulated offset.

    Accepts an optional ``backend`` to replace the default mock behavior,
    enabling stateful testing with ``StatefulPtp4l``.

    Attributes:
        backend: Optional replacement backend for stateful testing.
    """

    backend: Optional[MockGptpBackend] = field(default=None, repr=False)

    _internal_backend: MockGptpBackend = field(init=False, repr=False)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        self._internal_backend = self.backend or MockGptpBackend()

    @classmethod
    def client(cls) -> str:
        """Return the fully-qualified client class path."""
        return "jumpstarter_driver_gptp.client.GptpClient"

    @export
    async def start(self) -> None:
        """Start mock PTP synchronization.

        Raises:
            RuntimeError: If already running.
        """
        self._internal_backend.start()
        self.logger.info("MockGptp started")

    @export
    async def stop(self) -> None:
        """Stop mock PTP synchronization.

        Raises:
            RuntimeError: If not started.
        """
        self._internal_backend.stop()
        self.logger.info("MockGptp stopped")

    @export
    @validate_call(validate_return=True)
    def status(self) -> GptpStatus:
        """Query the current PTP synchronization status.

        Returns:
            Current synchronization status.

        Raises:
            RuntimeError: If not started.
        """
        b = self._internal_backend
        b.require_started()
        return GptpStatus(
            port_state=PortState(b._port_state),
            offset_ns=b._offset_ns,
            mean_delay_ns=567.0,
            servo_state=ServoState(b._servo_state),
        )

    @export
    @validate_call(validate_return=True)
    def get_offset(self) -> GptpOffset:
        """Get the current clock offset from master.

        Returns:
            Simulated offset measurement.

        Raises:
            RuntimeError: If not started.
        """
        b = self._internal_backend
        b.require_started()
        return GptpOffset(
            offset_from_master_ns=b._offset_ns,
            mean_path_delay_ns=567.0,
            freq_ppb=1234.0,
            timestamp=time.time(),
        )

    @export
    @validate_call(validate_return=True)
    def get_port_stats(self) -> GptpPortStats:
        """Get PTP port statistics.

        Returns:
            Simulated port statistics.

        Raises:
            RuntimeError: If not started.
        """
        self._internal_backend.require_started()
        return GptpPortStats(sync_count=42)

    @export
    @validate_call(validate_return=True)
    def get_clock_identity(self) -> str:
        """Get this clock's identity string.

        Returns:
            Simulated EUI-64 clock identity.

        Raises:
            RuntimeError: If not started.
        """
        self._internal_backend.require_started()
        return "aa:bb:cc:ff:fe:dd:ee:ff"

    @export
    @validate_call(validate_return=True)
    def get_parent_info(self) -> GptpParentInfo:
        """Get information about the parent/grandmaster clock.

        Returns:
            Simulated parent clock information.

        Raises:
            RuntimeError: If not started.
        """
        self._internal_backend.require_started()
        return GptpParentInfo(
            grandmaster_identity="11:22:33:ff:fe:44:55:66",
            grandmaster_priority1=128,
        )

    @export
    @validate_call(validate_return=True)
    def set_priority1(self, priority: int) -> None:
        """Set clock priority1 and simulate BMCA role change.

        Args:
            priority: Priority1 value (0-255).

        Raises:
            RuntimeError: If not started.
        """
        self._internal_backend.set_priority1(priority)

    @export
    @validate_call(validate_return=True)
    def is_synchronized(self) -> bool:
        """Check whether PTP is synchronized.

        Returns:
            True if port is SLAVE and servo is s2.

        Raises:
            RuntimeError: If not started.
        """
        b = self._internal_backend
        b.require_started()
        return b._port_state == "SLAVE" and b._servo_state == "s2"

    @export
    async def read(self) -> AsyncGenerator[GptpSyncEvent, None]:
        """Stream simulated sync events.

        Yields events indefinitely until the session is cancelled.

        Yields:
            Simulated sync events with mock offset/delay values.

        Raises:
            RuntimeError: If not started.
        """
        b = self._internal_backend
        b.require_started()
        while True:
            yield GptpSyncEvent(
                event_type="sync",
                port_state=PortState(b._port_state) if b._port_state in PortState.__members__ else None,
                servo_state=ServoState(b._servo_state) if b._servo_state in ("s0", "s1", "s2") else None,
                offset_ns=b._offset_ns,
                path_delay_ns=567.0,
                freq_ppb=1234.0,
                timestamp=time.time(),
            )
            await asyncio.sleep(0.1)
