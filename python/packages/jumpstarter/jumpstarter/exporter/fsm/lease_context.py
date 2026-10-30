"""Lease-local session references and requests; resource lifetime belongs to its owner."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jumpstarter.exporter.session import Session


@dataclass
class LeaseContext:
    """Live lease references and an external EndSession request, without phase flags."""

    lease_name: str
    session: "Session | None" = None
    socket_path: str = ""
    hook_socket_path: str = ""  # Separate socket for hook j commands to avoid SSL corruption
    client_name: str = ""
    end_session_requested: bool = False
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
