from __future__ import annotations

import logging
import time
from dataclasses import field

from opensomeip import ClientConfig, TransportMode
from opensomeip import SomeIpClient as OsipClient
from opensomeip.message import Message
from opensomeip.sd import SdConfig, ServiceInstance
from opensomeip.transport import Endpoint
from opensomeip.types import MessageId
from pydantic import ConfigDict, validate_call
from pydantic.dataclasses import dataclass

from .common import (
    SomeIpEventNotification,
    SomeIpMessageResponse,
    SomeIpPayload,
    SomeIpServiceEntry,
)
from jumpstarter.driver import Driver, export

logger = logging.getLogger(__name__)


def _message_to_response(msg: Message) -> SomeIpMessageResponse:
    return SomeIpMessageResponse(
        service_id=msg.message_id.service_id,
        method_id=msg.message_id.method_id,
        client_id=msg.request_id.client_id,
        session_id=msg.request_id.session_id,
        protocol_version=int(msg.protocol_version),
        interface_version=msg.interface_version,
        message_type=int(msg.message_type),
        return_code=int(msg.return_code),
        payload=msg.payload.hex(),
    )


@dataclass(kw_only=True, config=ConfigDict(arbitrary_types_allowed=True))
class SomeIp(Driver):
    """SOME/IP driver wrapping the opensomeip Python binding.

    Provides remote access to SOME/IP protocol operations including
    RPC calls, service discovery, raw messaging, and event subscriptions
    per the SOME/IP specification.
    """

    host: str
    port: int = 30490
    transport_mode: str = "UDP"
    multicast_group: str = "239.127.0.1"
    multicast_port: int = 30490

    _osip_client: OsipClient = field(init=False, repr=False)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_someip.client.SomeIpDriverClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        mode = TransportMode.TCP if self.transport_mode.upper() == "TCP" else TransportMode.UDP
        config = ClientConfig(
            local_endpoint=Endpoint(self.host, self.port),
            sd_config=SdConfig(
                multicast_endpoint=Endpoint(self.multicast_group, self.multicast_port),
                unicast_endpoint=Endpoint(self.host, self.port),
            ),
            transport_mode=mode,
        )
        self._osip_client = OsipClient(config)
        self._osip_client.start()

    def close(self):
        """Stop the opensomeip client."""
        try:
            self._osip_client.stop()
        except Exception:
            logger.warning("failed to close opensomeip client", exc_info=True)
        super().close()

    # --- RPC ---

    @export
    @validate_call(validate_return=True)
    def rpc_call(
        self,
        service_id: int,
        method_id: int,
        payload: SomeIpPayload,
        timeout: float = 5.0,
    ) -> SomeIpMessageResponse:
        """Make a SOME/IP RPC call and return the response."""
        response = self._osip_client.call(
            MessageId(service_id, method_id),
            payload=bytes.fromhex(payload.data),
            timeout=timeout,
        )
        return _message_to_response(response)

    # --- Raw Messaging ---

    @export
    @validate_call(validate_return=True)
    def send_message(
        self,
        service_id: int,
        method_id: int,
        payload: SomeIpPayload,
    ) -> None:
        """Send a raw SOME/IP message."""
        msg = Message(
            message_id=MessageId(service_id, method_id),
            payload=bytes.fromhex(payload.data),
        )
        self._osip_client.send(msg)

    @export
    @validate_call(validate_return=True)
    def receive_message(self, timeout: float = 2.0) -> SomeIpMessageResponse:
        """Receive a raw SOME/IP message."""
        import queue

        receiver = self._osip_client.transport.receiver
        try:
            msg = receiver._sync_queue.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"No message received within {timeout}s") from None
        return _message_to_response(msg)

    # --- Service Discovery ---

    @export
    @validate_call(validate_return=True)
    def find_service(
        self,
        service_id: int,
        instance_id: int = 0xFFFF,
        timeout: float = 5.0,
    ) -> list[SomeIpServiceEntry]:
        """Find services via SOME/IP-SD."""
        service = ServiceInstance(
            service_id=service_id,
            instance_id=instance_id,
        )
        found: list[SomeIpServiceEntry] = []

        def on_found(svc: ServiceInstance) -> None:
            found.append(
                SomeIpServiceEntry(
                    service_id=svc.service_id,
                    instance_id=svc.instance_id,
                    major_version=svc.major_version,
                    minor_version=svc.minor_version,
                )
            )

        self._osip_client.find(service, callback=on_found)
        time.sleep(timeout)
        return found

    @export
    @validate_call(validate_return=True)
    def subscribe_eventgroup(self, service_id: int, eventgroup_id: int) -> None:
        """Subscribe to a SOME/IP event group."""
        self._osip_client.subscribe_events(eventgroup_id)

    @export
    @validate_call(validate_return=True)
    def unsubscribe_eventgroup(self, service_id: int, eventgroup_id: int) -> None:
        """Unsubscribe from a SOME/IP event group."""
        self._osip_client.unsubscribe_events(eventgroup_id)

    @export
    @validate_call(validate_return=True)
    def receive_event(self, timeout: float = 5.0) -> SomeIpEventNotification:
        """Receive the next event notification."""
        import queue

        receiver = self._osip_client.event_subscriber.notifications()
        try:
            msg = receiver._sync_queue.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"No event received within {timeout}s") from None
        return SomeIpEventNotification(
            service_id=msg.message_id.service_id,
            event_id=msg.message_id.method_id,
            payload=msg.payload.hex(),
        )

    # --- Connection Management ---

    @export
    @validate_call(validate_return=True)
    def close_connection(self) -> None:
        """Close the SOME/IP connection."""
        self._osip_client.stop()

    @export
    @validate_call(validate_return=True)
    def reconnect(self) -> None:
        """Reconnect to the SOME/IP endpoint."""
        self._osip_client.stop()
        self._osip_client.start()
