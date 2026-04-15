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

        Raises:
            RuntimeError: If ptp4l is already running.
        """
        self.call("start")

    def stop(self) -> None:
        """Stop PTP synchronization.

        Terminates ptp4l and phc2sys processes and cleans up temp files.

        Raises:
            RuntimeError: If ptp4l is not started.
        """
        self.call("stop")

    def status(self) -> GptpStatus:
        """Query the current PTP synchronization status.

        Returns:
            Current synchronization status including port state,
            offset, delay, and servo state.

        Raises:
            RuntimeError: If ptp4l is not started.
        """
        return GptpStatus.model_validate(self.call("status"))

    def get_offset(self) -> GptpOffset:
        """Get the current clock offset from master.

        Returns:
            Offset measurement including path delay and frequency.

        Raises:
            RuntimeError: If ptp4l is not started.
        """
        return GptpOffset.model_validate(self.call("get_offset"))

    def get_port_stats(self) -> GptpPortStats:
        """Get PTP port statistics.

        Returns:
            Port statistics counters.

        Raises:
            RuntimeError: If ptp4l is not started.
        """
        return GptpPortStats.model_validate(self.call("get_port_stats"))

    def get_clock_identity(self) -> str:
        """Get this clock's identity string.

        Returns:
            Clock identity as EUI-64 string.

        Raises:
            RuntimeError: If ptp4l is not started.
            NotImplementedError: If the real driver has no UDS integration.
        """
        return self.call("get_clock_identity")

    def get_parent_info(self) -> GptpParentInfo:
        """Get information about the parent/grandmaster clock.

        Returns:
            Parent and grandmaster clock information.

        Raises:
            RuntimeError: If ptp4l is not started.
            NotImplementedError: If the real driver has no UDS integration.
        """
        return GptpParentInfo.model_validate(self.call("get_parent_info"))

    def set_priority1(self, priority: int) -> None:
        """Set clock priority1 to influence BMCA master election.

        Lower values make this clock more likely to become grandmaster.

        Args:
            priority: Priority1 value (0-255).

        Raises:
            RuntimeError: If ptp4l is not started.
            NotImplementedError: If the real driver has no UDS integration.
        """
        self.call("set_priority1", priority)

    def is_synchronized(self) -> bool:
        """Check whether PTP is synchronized (servo locked in SLAVE state).

        Returns:
            True if synchronized.

        Raises:
            RuntimeError: If ptp4l is not started.
        """
        return self.call("is_synchronized")

    def wait_for_sync(
        self,
        timeout: float = 30.0,
        poll_interval: float = 1.0,
        threshold_ns: float | None = None,
    ) -> bool:
        """Block until PTP synchronization is achieved or timeout expires.

        Only catches ``RuntimeError`` (driver not-yet-ready) during polling.
        Transport or unexpected failures propagate immediately.

        Args:
            timeout: Maximum time to wait in seconds.
            poll_interval: Polling interval in seconds.
            threshold_ns: If provided, also require the absolute offset
                from master to be below this value (in nanoseconds) before
                returning True.

        Returns:
            True if synchronized before timeout, False otherwise.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.is_synchronized():
                    if threshold_ns is not None:
                        offset = self.get_offset()
                        if abs(offset.offset_from_master_ns) >= threshold_ns:
                            time.sleep(poll_interval)
                            continue
                    return True
            except RuntimeError:
                pass
            time.sleep(poll_interval)
        return False

    def monitor(self) -> Generator[GptpSyncEvent, None, None]:
        """Stream PTP sync status updates.

        Yields ``GptpSyncEvent`` objects with current offset, delay, and state.

        Yields:
            Sync event updates.
        """
        for v in self.streamingcall("read"):
            yield GptpSyncEvent.model_validate(v)

    def cli(self):
        """Build the Click CLI group for gPTP commands.

        Returns:
            Click group with start, stop, status, offset, monitor,
            and set-priority commands.
        """

        @driver_click_group(self)
        def base():
            """gPTP/PTP time synchronization"""
            pass

        @base.command()
        def start():
            """Start PTP synchronization."""
            self.start()
            click.echo("PTP synchronization started")

        @base.command()
        def stop():
            """Stop PTP synchronization."""
            self.stop()
            click.echo("PTP synchronization stopped")

        @base.command()
        def status():
            """Show PTP synchronization status."""
            s = self.status()
            click.echo(f"Port state:    {s.port_state.value}")
            click.echo(f"Servo state:   {s.servo_state.value}")
            click.echo(f"Offset:        {s.offset_ns:.0f} ns")
            click.echo(f"Mean delay:    {s.mean_delay_ns:.0f} ns")
            click.echo(f"Synchronized:  {self.is_synchronized()}")

        @base.command()
        def offset():
            """Show current clock offset from master."""
            o = self.get_offset()
            click.echo(f"Offset:      {o.offset_from_master_ns:.0f} ns")
            click.echo(f"Path delay:  {o.mean_path_delay_ns:.0f} ns")
            click.echo(f"Freq adj:    {o.freq_ppb:.0f} ppb")

        @base.command()
        @click.option("--count", "-n", default=10, help="Number of events to show")
        def monitor(count):
            """Monitor PTP sync events."""
            for i, event in enumerate(self.monitor()):
                click.echo(
                    f"[{event.event_type}] state={event.port_state} "
                    f"offset={event.offset_ns:.0f}ns "
                    f"delay={event.path_delay_ns:.0f}ns"
                )
                if i + 1 >= count:
                    break

        @base.command(name="port-stats")
        def port_stats():
            """Show PTP port statistics."""
            s = self.get_port_stats()
            click.echo(f"Sync count:        {s.sync_count}")
            click.echo(f"Follow-up count:   {s.followup_count}")
            click.echo(f"PDelay req count:  {s.pdelay_req_count}")
            click.echo(f"PDelay resp count: {s.pdelay_resp_count}")
            click.echo(f"Announce count:    {s.announce_count}")

        @base.command(name="set-priority")
        @click.argument("priority", type=int)
        def set_priority(priority):
            """Set clock priority1 for BMCA."""
            self.set_priority1(priority)
            click.echo(f"Priority1 set to {priority}")

        return base
