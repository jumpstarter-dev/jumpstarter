"""LeaseScope: Context manager for lease-related resources.

This module provides a clean abstraction for managing the lifecycle of resources
associated with a lease, including the session, socket path, and synchronization events.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from anyio import Event

from jumpstarter.common import ExporterStatus
from jumpstarter.exporter.lease_lifecycle import LeaseLifecycle

if TYPE_CHECKING:
    from jumpstarter.exporter.session import Session


@dataclass
class LeaseContext:
    """Encapsulates all resources associated with an active lease.

    This class bundles together the session, socket path, lifecycle controller,
    and lease identity information that are needed throughout the lease lifecycle.
    By grouping these resources, we make their relationships and lifecycles explicit.

    Attributes:
        lease_name: Name of the current lease assigned by the controller
        lifecycle: LeaseLifecycle FSM that coordinates all lease phase transitions
        end_session_requested: Event that signals when client requests end session (gRPC layer)
        session: The Session object managing the device and gRPC services (set in handle_lease)
        socket_path: Unix socket path where the session is serving (set in handle_lease)
        hook_socket_path: Separate Unix socket for hook j commands to avoid SSL frame corruption
        client_name: Name of the client currently holding the lease (empty if unleased)
        current_status: Current exporter status (stored here for access before session is created)
        status_message: Message describing the current status
    """

    lease_name: str
    lifecycle: LeaseLifecycle = field(default_factory=LeaseLifecycle)
    end_session_requested: Event = field(default_factory=Event)
    session: "Session | None" = None
    socket_path: str = ""
    hook_socket_path: str = ""
    client_name: str = field(default="")
    current_status: ExporterStatus = field(default=ExporterStatus.AVAILABLE)
    status_message: str = field(default="")

    @property
    def skip_after_lease_hook(self) -> bool:
        return self.lifecycle.skip_after_lease

    @skip_after_lease_hook.setter
    def skip_after_lease_hook(self, value: bool) -> None:
        self.lifecycle.skip_after_lease = value

    def __post_init__(self):
        """Validate that required resources are present."""
        assert self.lease_name, "LeaseScope requires a non-empty lease_name"

    def is_ready(self) -> bool:
        """Check if the lease scope has been fully initialized with session and socket.

        Note: This checks for resource initialization (session/socket), not lease activity.
        Use is_active() to check if the lease itself is active.
        """
        return self.session is not None and self.socket_path != ""

    def is_active(self) -> bool:
        """Check if this lease is active (has a non-empty lease name)."""
        return bool(self.lease_name)

    def has_client(self) -> bool:
        """Check if a client is currently holding the lease."""
        return bool(self.client_name)

    def update_client(self, client_name: str):
        """Update the client name for this lease."""
        self.client_name = client_name

    def clear_client(self):
        """Clear the client name when the lease is no longer held."""
        self.client_name = ""

    def update_status(self, status: ExporterStatus, message: str = ""):
        """Update the current status in the lease context.

        This stores the status in the LeaseContext so it's available even before
        the session is created, fixing the race condition where GetStatus is called
        before the session can be updated.
        """
        self.current_status = status
        self.status_message = message
        if self.session:
            self.session.update_status(status, message)

    def drivers_ready(self) -> bool:
        """Check if drivers are ready for use (lifecycle has reached READY or later).

        Returns True if the lease lifecycle has passed the READY gate and drivers
        can be accessed. Used by Session to gate driver calls during hook execution.
        """
        return self.lifecycle.drivers_ready()

    async def wait_for_drivers(self) -> None:
        """Wait for drivers to be ready (lifecycle reaches READY phase).

        This method blocks until the beforeLease hook completes and the lifecycle
        transitions to READY, allowing clients to connect early but wait for
        driver access.
        """
        await self.lifecycle.wait_ready()
