from __future__ import annotations

import time
from collections.abc import Generator
from dataclasses import dataclass

import click

from .common import (
    GptpOffset,
    GptpParentInfo,
    GptpPortStats,
    GptpStatus,
    GptpSyncEvent,
)
from jumpstarter.client import DriverClient
from jumpstarter.client.decorators import driver_click_group


@dataclass(kw_only=True)
class GptpClient(DriverClient):
    """Client interface for gPTP/PTP time synchronization.

    Provides methods to manage PTP synchronization lifecycle, query status,
    monitor sync events, and configure clock priority for BMCA master election.
    """

    def start(self) -> None:
        """Start PTP synchronization on the exporter host.

        Spawns ptp4l (and optionally phc2sys) as managed subprocesses.

        :raises RuntimeError: If ptp4l is already running
        """
        self.call("start")

    def stop(self) -> None:
        """Stop PTP synchronization.

        Terminates ptp4l and phc2sys processes.

        :raises RuntimeError: If ptp4l is not started
        """
        self.call("stop")

    def status(self) -> GptpStatus:
        """Query the current PTP synchronization status.

        Returns the port state, clock class, current offset from master,
        mean path delay, and servo state.

        :returns: Current synchronization status
        :rtype: GptpStatus
        :raises RuntimeError: If ptp4l is not started
        """
        return GptpStatus.model_validate(self.call("status"))

    def get_offset(self) -> GptpOffset:
        """Get the current clock offset from master.

        :returns: Offset measurement including path delay and frequency
        :rtype: GptpOffset
        :raises RuntimeError: If ptp4l is not started
        """
        return GptpOffset.model_validate(self.call("get_offset"))

    def get_port_stats(self) -> GptpPortStats:
        """Get PTP port statistics.

        :returns: Port statistics counters
        :rtype: GptpPortStats
        :raises RuntimeError: If ptp4l is not started
        """
        return GptpPortStats.model_validate(self.call("get_port_stats"))

    def get_clock_identity(self) -> str:
        """Get this clock's identity string.

        :returns: Clock identity
        :rtype: str
        :raises RuntimeError: If ptp4l is not started
        """
        return self.call("get_clock_identity")

    def get_parent_info(self) -> GptpParentInfo:
        """Get information about the parent/grandmaster clock.

        :returns: Parent and grandmaster clock information
        :rtype: GptpParentInfo
        :raises RuntimeError: If ptp4l is not started
        """
        return GptpParentInfo.model_validate(self.call("get_parent_info"))

    def set_priority1(self, priority: int) -> None:
        """Set clock priority1 to influence BMCA master election.

        Lower values make this clock more likely to become grandmaster.

        :param priority: Priority1 value (0-255)
        :raises RuntimeError: If ptp4l is not started
        """
        self.call("set_priority1", priority)

    def is_synchronized(self) -> bool:
        """Check whether PTP is synchronized (servo locked in SLAVE state).

        :returns: True if synchronized
        :rtype: bool
        :raises RuntimeError: If ptp4l is not started
        """
        return self.call("is_synchronized")

    def wait_for_sync(self, timeout: float = 30.0, poll_interval: float = 1.0) -> bool:
        """Block until PTP synchronization is achieved or timeout expires.

        :param timeout: Maximum time to wait in seconds
        :param poll_interval: Polling interval in seconds
        :returns: True if synchronized, False if timeout expired
        :rtype: bool
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.is_synchronized():
                    return True
            except Exception:
                pass
            time.sleep(poll_interval)
        return False

    def monitor(self) -> Generator[GptpSyncEvent, None, None]:
        """Stream PTP sync status updates.

        Yields GptpSyncEvent objects with current offset, delay, and state.

        :yields: Sync event updates
        """
        for v in self.streamingcall("read"):
            yield GptpSyncEvent.model_validate(v)

    def cli(self):
        @driver_click_group(self)
        def base():
            """gPTP/PTP time synchronization"""
            pass

        @base.command()
        def start():
            """Start PTP synchronization"""
            self.start()
            click.echo("PTP synchronization started")

        @base.command()
        def stop():
            """Stop PTP synchronization"""
            self.stop()
            click.echo("PTP synchronization stopped")

        @base.command()
        def status():
            """Show PTP synchronization status"""
            s = self.status()
            click.echo(f"Port state:    {s.port_state.value}")
            click.echo(f"Servo state:   {s.servo_state.value}")
            click.echo(f"Offset:        {s.offset_ns:.0f} ns")
            click.echo(f"Mean delay:    {s.mean_delay_ns:.0f} ns")
            click.echo(f"Synchronized:  {self.is_synchronized()}")

        @base.command()
        def offset():
            """Show current clock offset from master"""
            o = self.get_offset()
            click.echo(f"Offset:      {o.offset_from_master_ns:.0f} ns")
            click.echo(f"Path delay:  {o.mean_path_delay_ns:.0f} ns")
            click.echo(f"Freq adj:    {o.freq_ppb:.0f} ppb")

        @base.command()
        @click.option("--count", "-n", default=10, help="Number of events to show")
        def monitor(count):
            """Monitor PTP sync events"""
            for i, event in enumerate(self.monitor()):
                click.echo(
                    f"[{event.event_type}] state={event.port_state} "
                    f"offset={event.offset_ns:.0f}ns "
                    f"delay={event.path_delay_ns:.0f}ns"
                )
                if i + 1 >= count:
                    break

        @base.command(name="set-priority")
        @click.argument("priority", type=int)
        def set_priority(priority):
            """Set clock priority1 for BMCA"""
            self.set_priority1(priority)
            click.echo(f"Priority1 set to {priority}")

        return base
