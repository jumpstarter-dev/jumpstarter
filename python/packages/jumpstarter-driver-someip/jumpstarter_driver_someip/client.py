from __future__ import annotations

from dataclasses import dataclass

from .common import (
    SomeIpEventNotification,
    SomeIpMessageResponse,
    SomeIpPayload,
    SomeIpServiceEntry,
)
from jumpstarter.client import DriverClient


@dataclass(kw_only=True)
class SomeIpDriverClient(DriverClient):
    """Client interface for SOME/IP operations.

    Provides methods for RPC calls, raw messaging, service discovery,
    and event subscriptions over the Jumpstarter remoting layer.
    """

    def start(self) -> None:
        """Force start the SOME/IP client."""
        self.call("start")

    # --- RPC ---

    def rpc_call(
        self,
        service_id: int,
        method_id: int,
        payload: bytes,
        timeout: float = 5.0,
    ) -> SomeIpMessageResponse:
        """Make a SOME/IP RPC call and return the response."""
        msg = SomeIpPayload(data=payload.hex())
        return SomeIpMessageResponse.model_validate(
            self.call("rpc_call", service_id, method_id, msg, timeout)
        )

    # --- Raw Messaging ---

    def send_message(
        self,
        service_id: int,
        method_id: int,
        payload: bytes,
    ) -> None:
        """Send a raw SOME/IP message."""
        msg = SomeIpPayload(data=payload.hex())
        self.call("send_message", service_id, method_id, msg)

    def receive_message(self, timeout: float = 2.0) -> SomeIpMessageResponse:
        """Receive a raw SOME/IP message."""
        return SomeIpMessageResponse.model_validate(
            self.call("receive_message", timeout)
        )

    # --- Service Discovery ---

    def find_service(
        self,
        service_id: int,
        instance_id: int = 0xFFFF,
        timeout: float = 5.0,
    ) -> list[SomeIpServiceEntry]:
        """Find services via SOME/IP-SD."""
        result = self.call("find_service", service_id, instance_id, timeout)
        return [SomeIpServiceEntry.model_validate(v) for v in result]

    # --- Events ---

    def subscribe_eventgroup(self, eventgroup_id: int) -> None:
        """Subscribe to a SOME/IP event group."""
        self.call("subscribe_eventgroup", eventgroup_id)

    def unsubscribe_eventgroup(self, eventgroup_id: int) -> None:
        """Unsubscribe from a SOME/IP event group."""
        self.call("unsubscribe_eventgroup", eventgroup_id)

    def receive_event(self, timeout: float = 5.0) -> SomeIpEventNotification:
        """Receive the next event notification."""
        return SomeIpEventNotification.model_validate(
            self.call("receive_event", timeout)
        )

    # --- Connection Management ---

    def close_connection(self) -> None:
        """Close the SOME/IP connection."""
        self.call("close_connection")

    def reconnect(self) -> None:
        """Reconnect to the SOME/IP endpoint."""
        self.call("reconnect")
