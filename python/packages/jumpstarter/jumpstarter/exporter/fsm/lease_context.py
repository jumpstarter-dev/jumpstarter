"""Lease-local session references and requests; resource lifetime belongs to its owner."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from jumpstarter.exporter.lease_hooks import HookPlan

if TYPE_CHECKING:
    from jumpstarter.exporter.session import Session


@dataclass
class LeaseContext:
    """Live lease references and an external EndSession request, without phase flags."""

    lease_name: str
    lease_uid: str = ""
    """UID of that lease; tells it apart from an earlier lease with the same name"""
    session: "Session | None" = None
    socket_path: str = ""
    hook_socket_path: str = ""  # Separate socket for hook j commands to avoid SSL corruption
    client_name: str = ""
    end_session_requested: bool = False
    skip_after_lease_hook: bool = False
    """Set when no afterLease hook is owed: its setup never ran, or failed explicitly."""
    hooks: HookPlan = field(default_factory=HookPlan)
    """How this process runs the lease's hooks, and what the controller's record shows."""
    wake: Callable[[str], None] | None = None

    def __post_init__(self) -> None:
        if not self.lease_name:
            raise ValueError("A lease needs a non-empty lease_name")

    def request_end_session(self) -> None:
        """Retain this session's request and wake its lifecycle in the same loop turn."""
        if not self.end_session_requested:
            self.end_session_requested = True
            if self.wake is not None:
                self.wake("EndSession")
