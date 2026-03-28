from __future__ import annotations

import asyncio
import logging
import re
import subprocess
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
    """Parse a single ptp4l log line into structured data."""
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
) -> str:
    """Generate ptp4l configuration file content."""
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
        lines.append("priority1\t\t0")
        lines.append("priority2\t\t0")

    lines.append(f"\n[{interface}]")
    return "\n".join(lines) + "\n"


@dataclass(kw_only=True, config=ConfigDict(arbitrary_types_allowed=True))
class Gptp(Driver):
    """gPTP/PTP driver managing linuxptp (ptp4l/phc2sys) for time synchronization.

    Provides lifecycle management, status monitoring, and configuration of
    IEEE 802.1AS (gPTP) or IEEE 1588 (PTPv2) time synchronization between
    the exporter host and a target device.
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
    _config_file: Optional[tempfile.NamedTemporaryFile] = field(
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

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_gptp.client.GptpClient"

    def _supports_hw_timestamping(self) -> bool:
        try:
            result = subprocess.run(
                ["ethtool", "-T", self.interface],
                capture_output=True,
                text=True,
            )
            output = result.stdout
            return "hardware-transmit" in output and "hardware-receive" in output
        except FileNotFoundError:
            return False

    def _require_started(self) -> None:
        if self._ptp4l_proc is None:
            raise RuntimeError("ptp4l not started -- call start() first")

    async def _read_ptp4l_output(self) -> None:
        """Background task: read ptp4l stdout and update internal state."""
        proc = self._ptp4l_proc
        if proc is None or proc.stdout is None:
            return
        while True:
            raw = await proc.stdout.readline()
            if not raw:
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

    @export
    async def start(self) -> None:
        """Start PTP synchronization by spawning ptp4l (and optionally phc2sys)."""
        if self._ptp4l_proc is not None:
            raise RuntimeError("ptp4l already running")

        config_content = _generate_ptp4l_config(
            interface=self.interface,
            domain=self.domain,
            profile=self.profile,
            transport=self.transport,
            role=self.role,
        )

        self._config_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".cfg", prefix="ptp4l_", delete=False
        )
        self._config_file.write(config_content)
        self._config_file.flush()

        hw_ts = self._supports_hw_timestamping()
        ts_flag = "-H" if hw_ts else "-S"
        if not hw_ts:
            self.logger.warning(
                "Hardware timestamping not available on %s, falling back to software timestamping",
                self.interface,
            )

        cmd = [
            "ptp4l",
            "-f", self._config_file.name,
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
        )

        self._port_state = "INITIALIZING"
        self._servo_state = "s0"
        self._priority1 = 128
        self._stats = {}
        self._reader_task = asyncio.get_event_loop().create_task(
            self._read_ptp4l_output()
        )

        await asyncio.sleep(0.5)
        if self._ptp4l_proc.returncode is not None:
            raise RuntimeError(
                f"ptp4l exited immediately with code {self._ptp4l_proc.returncode}"
            )

        if self.sync_system_clock and hw_ts:
            phc2sys_cmd = ["phc2sys", "-a", "-rr", "-m"]
            self.logger.info("Starting phc2sys: %s", " ".join(phc2sys_cmd))
            self._phc2sys_proc = await asyncio.create_subprocess_exec(
                *phc2sys_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

    @export
    async def stop(self) -> None:
        """Stop PTP synchronization."""
        self._require_started()

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
            self._phc2sys_proc = None

        if self._ptp4l_proc is not None:
            self._ptp4l_proc.terminate()
            try:
                await asyncio.wait_for(self._ptp4l_proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._ptp4l_proc.kill()
            self._ptp4l_proc = None

        if self._config_file is not None:
            import os
            try:
                os.unlink(self._config_file.name)
            except OSError:
                pass
            self._config_file = None

        self._port_state = "INITIALIZING"
        self._servo_state = "s0"

    @export
    @validate_call(validate_return=True)
    def status(self) -> GptpStatus:
        """Query the current PTP synchronization status.

        :returns: Current synchronization status
        :rtype: GptpStatus
        :raises RuntimeError: If ptp4l is not started
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

        :returns: Offset measurement
        :rtype: GptpOffset
        :raises RuntimeError: If ptp4l is not started
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

        :returns: Port statistics counters
        :rtype: GptpPortStats
        :raises RuntimeError: If ptp4l is not started
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

        :returns: Clock identity
        :rtype: str
        :raises RuntimeError: If ptp4l is not started
        """
        self._require_started()
        return ""

    @export
    @validate_call(validate_return=True)
    def get_parent_info(self) -> GptpParentInfo:
        """Get information about the parent/grandmaster clock.

        :returns: Parent and grandmaster clock information
        :rtype: GptpParentInfo
        :raises RuntimeError: If ptp4l is not started
        """
        self._require_started()
        return GptpParentInfo()

    @export
    @validate_call(validate_return=True)
    def set_priority1(self, priority: int) -> None:
        """Set clock priority1 to influence BMCA master election.

        Lower values make this clock more likely to become grandmaster.

        :param priority: Priority1 value (0-255)
        :raises RuntimeError: If ptp4l is not started
        """
        self._require_started()
        self._priority1 = priority
        self.logger.info("Set priority1 to %d", priority)

    @export
    @validate_call(validate_return=True)
    def is_synchronized(self) -> bool:
        """Check whether PTP is synchronized (servo locked in SLAVE state).

        :returns: True if synchronized
        :rtype: bool
        :raises RuntimeError: If ptp4l is not started
        """
        self._require_started()
        return self._port_state == "SLAVE" and self._servo_state == "s2"

    @export
    async def read(self) -> AsyncGenerator[GptpSyncEvent, None]:
        """Stream periodic sync status updates.

        Yields a GptpSyncEvent approximately once per second with current
        offset, delay, and state information.
        """
        self._require_started()
        prev_state = self._port_state
        for _ in range(100):
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
    """Default backend for MockGptp. Can be replaced with StatefulPtp4l for stateful testing."""

    def __init__(self):
        self._started = False
        self._port_state = "INITIALIZING"
        self._servo_state = "s0"
        self._offset_ns = 0.0
        self._priority1 = 128

    def require_started(self):
        if not self._started:
            raise RuntimeError("ptp4l not started -- call start() first")

    def start(self):
        if self._started:
            raise RuntimeError("ptp4l already running")
        self._started = True
        self._port_state = "SLAVE"
        self._servo_state = "s2"
        self._offset_ns = -23.0
        self._priority1 = 128

    def stop(self):
        self.require_started()
        self._started = False
        self._port_state = "INITIALIZING"
        self._servo_state = "s0"
        self._offset_ns = 0.0

    def set_priority1(self, priority: int):
        self.require_started()
        self._priority1 = priority
        if priority < 128 and self._port_state in ("SLAVE", "LISTENING", "PASSIVE"):
            self._port_state = "MASTER"


@dataclass(kw_only=True, config=ConfigDict(arbitrary_types_allowed=True))
class MockGptp(Driver):
    """Mock gPTP driver for testing without real PTP hardware.

    Simulates PTP synchronization behavior: after start(), immediately enters
    SLAVE state with a small simulated offset.

    Accepts an optional ``backend`` to replace the default mock behavior,
    enabling stateful testing with ``StatefulPtp4l``.
    """

    backend: Optional[MockGptpBackend] = field(default=None, repr=False)

    _internal_backend: MockGptpBackend = field(init=False, repr=False)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        self._internal_backend = self.backend or MockGptpBackend()

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_gptp.client.GptpClient"

    @export
    async def start(self) -> None:
        """Start mock PTP synchronization."""
        self._internal_backend.start()
        self.logger.info("MockGptp started")

    @export
    async def stop(self) -> None:
        """Stop mock PTP synchronization."""
        self._internal_backend.stop()
        self.logger.info("MockGptp stopped")

    @export
    @validate_call(validate_return=True)
    def status(self) -> GptpStatus:
        """Query the current PTP synchronization status."""
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
        """Get the current clock offset from master."""
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
        """Get PTP port statistics."""
        self._internal_backend.require_started()
        return GptpPortStats(sync_count=42)

    @export
    @validate_call(validate_return=True)
    def get_clock_identity(self) -> str:
        """Get this clock's identity string."""
        self._internal_backend.require_started()
        return "aa:bb:cc:ff:fe:dd:ee:ff"

    @export
    @validate_call(validate_return=True)
    def get_parent_info(self) -> GptpParentInfo:
        """Get information about the parent/grandmaster clock."""
        self._internal_backend.require_started()
        return GptpParentInfo(
            grandmaster_identity="11:22:33:ff:fe:44:55:66",
            grandmaster_priority1=128,
        )

    @export
    @validate_call(validate_return=True)
    def set_priority1(self, priority: int) -> None:
        """Set clock priority1."""
        self._internal_backend.set_priority1(priority)

    @export
    @validate_call(validate_return=True)
    def is_synchronized(self) -> bool:
        """Check whether PTP is synchronized."""
        b = self._internal_backend
        b.require_started()
        return b._port_state == "SLAVE" and b._servo_state == "s2"

    @export
    async def read(self) -> AsyncGenerator[GptpSyncEvent, None]:
        """Stream simulated sync events."""
        b = self._internal_backend
        b.require_started()
        for _ in range(100):
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
