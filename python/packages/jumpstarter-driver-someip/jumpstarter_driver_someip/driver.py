from __future__ import annotations

import logging
import queue
import threading
from dataclasses import field

from opensomeip import ClientConfig, TransportMode
from opensomeip import SomeIpClient as OsipClient
from opensomeip.message import Message
from opensomeip.sd import SdConfig, ServiceInstance
from opensomeip.transport import Endpoint
from opensomeip.types import MessageId
from pydantic import ConfigDict, SkipValidation, validate_call
from pydantic.dataclasses import dataclass

from .common import (
    SomeIpEventNotification,
    SomeIpMessageResponse,
    SomeIpPayload,
    SomeIpServiceEntry,
)
from jumpstarter.driver import Driver, export

logger = logging.getLogger(__name__)

_VALID_TRANSPORT_MODES = {"TCP", "UDP"}


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


def _receive_from_queue(receiver: object, timeout: float, error_msg: str) -> Message:
    """Receive a message from a MessageReceiver's internal queue.

    opensomeip's MessageReceiver exposes __iter__/__next__ but no public
    blocking-with-timeout method. We access the internal _sync_queue as a
    pragmatic workaround until a public API is provided.
    """
    sync_queue = getattr(receiver, "_sync_queue", None)
    if sync_queue is None:
        raise RuntimeError("opensomeip MessageReceiver missing _sync_queue; API may have changed")
    try:
        return sync_queue.get(timeout=timeout)
    except queue.Empty:
        raise TimeoutError(error_msg) from None


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

    _osip_client: OsipClient | None = field(init=False, repr=False, default=None)
    _osip_config: ClientConfig = field(init=False, repr=False)
    _osip_lock: SkipValidation[threading.Lock] = field(init=False, repr=False, default_factory=threading.Lock)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_someip.client.SomeIpDriverClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        transport_upper = self.transport_mode.upper()
        if transport_upper not in _VALID_TRANSPORT_MODES:
            raise ValueError(
                f"Invalid transport_mode: {self.transport_mode!r}. Must be 'TCP' or 'UDP'."
            )
        mode = TransportMode.TCP if transport_upper == "TCP" else TransportMode.UDP

        self._osip_config = ClientConfig(
            local_endpoint=Endpoint(self.host, self.port),
            sd_config=SdConfig(
                multicast_endpoint=Endpoint(self.multicast_group, self.multicast_port),
                unicast_endpoint=Endpoint(self.host, self.port),
            ),
            transport_mode=mode,
        )

    def _ensure_client(self) -> OsipClient:
        """Create and start the OsipClient on first use (thread-safe)."""
        if self._osip_client is None:
            with self._osip_lock:
                if self._osip_client is None:
                    self._osip_client = OsipClient(self._osip_config)
                    self._osip_client.start()
        return self._osip_client

    def close(self):
        """Stop the opensomeip client."""
        if self._osip_client is not None:
            try:
                self._osip_client.stop()
            except Exception:
                logger.warning("failed to close opensomeip client", exc_info=True)
        super().close()

    # --- RPC ---

    @export
    @validate_call(validate_return=True)
    def start(self) -> None:
        """Force start the SOME/IP client."""
        self._ensure_client()

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
        response = self._ensure_client().call(
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
        self._ensure_client().send(msg)

    @export
    @validate_call(validate_return=True)
    def receive_message(self, timeout: float = 2.0) -> SomeIpMessageResponse:
        """Receive a raw SOME/IP message."""
        receiver = self._ensure_client().transport.receiver
        msg = _receive_from_queue(receiver, timeout, f"No message received within {timeout}s")
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
        event = threading.Event()
        lock = threading.Lock()

        def on_found(svc: ServiceInstance) -> None:
            with lock:
                found.append(
                    SomeIpServiceEntry(
                        service_id=svc.service_id,
                        instance_id=svc.instance_id,
                        major_version=svc.major_version,
                        minor_version=svc.minor_version,
                    )
                )
            event.set()

        self._ensure_client().find(service, callback=on_found)
        event.wait(timeout=timeout)
        with lock:
            return list(found)

    @export
    @validate_call(validate_return=True)
    def subscribe_eventgroup(self, eventgroup_id: int) -> None:
        """Subscribe to a SOME/IP event group."""
        self._ensure_client().subscribe_events(eventgroup_id)

    @export
    @validate_call(validate_return=True)
    def unsubscribe_eventgroup(self, eventgroup_id: int) -> None:
        """Unsubscribe from a SOME/IP event group."""
        self._ensure_client().unsubscribe_events(eventgroup_id)

    @export
    @validate_call(validate_return=True)
    def receive_event(self, timeout: float = 5.0) -> SomeIpEventNotification:
        """Receive the next event notification."""
        receiver = self._ensure_client().event_subscriber.notifications()
        msg = _receive_from_queue(receiver, timeout, f"No event received within {timeout}s")
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
        if self._osip_client is not None:
            try:
                self._osip_client.stop()
            except Exception:
                logger.warning("failed to stop opensomeip client during close_connection", exc_info=True)

    @export
    @validate_call(validate_return=True)
    def reconnect(self) -> None:
        """Reconnect to the SOME/IP endpoint."""
        if self._osip_client is not None:
            try:
                self._osip_client.stop()
            except Exception:
                logger.warning("failed to stop opensomeip client during reconnect", exc_info=True)
            self._osip_client.start()
        else:
            self._ensure_client()
