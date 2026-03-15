from __future__ import annotations

import logging
from dataclasses import field

from doipclient import DoIPClient
from pydantic import ConfigDict, validate_call
from pydantic.dataclasses import dataclass

from .common import (
    AliveCheckResponse,
    DiagnosticPayload,
    DiagnosticPowerModeResponse,
    EntityStatusResponse,
    RoutingActivationResponse,
    VehicleIdentificationResponse,
)
from jumpstarter.driver import Driver, export

logger = logging.getLogger(__name__)


@dataclass(kw_only=True, config=ConfigDict(arbitrary_types_allowed=True))
class DoIP(Driver):
    """
    Raw DoIP (Diagnostics over Internet Protocol) driver.

    Provides low-level DoIP operations such as vehicle discovery,
    entity status checks, alive checks, and raw diagnostic message
    exchange per ISO-13400.
    """

    ecu_ip: str
    ecu_logical_address: int
    tcp_port: int = 13400
    protocol_version: int = 2
    client_logical_address: int = 0x0E00
    auto_reconnect_tcp: bool = False
    activation_type: int | None = 0

    _doip_client: DoIPClient = field(init=False, repr=False)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_doip.client.DoIPDriverClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self._doip_client = DoIPClient(
            self.ecu_ip,
            self.ecu_logical_address,
            tcp_port=self.tcp_port,
            protocol_version=self.protocol_version,
            client_logical_address=self.client_logical_address,
            auto_reconnect_tcp=self.auto_reconnect_tcp,
            activation_type=self.activation_type,
        )

    def close(self):
        """Close the DoIP connection."""
        try:
            self._doip_client.close()
        except Exception:
            logger.warning("failed to close DoIP client", exc_info=True)
        super().close()

    @export
    @validate_call(validate_return=True)
    def entity_status(self) -> EntityStatusResponse:
        """Request the DoIP entity status."""
        resp = self._doip_client.request_entity_status()
        return EntityStatusResponse(
            node_type=resp.node_type,
            max_open_sockets=resp.max_open_sockets,
            currently_open_sockets=resp.currently_open_sockets,
            max_data_size=resp.max_data_size,
        )

    @export
    @validate_call(validate_return=True)
    def alive_check(self) -> AliveCheckResponse:
        """Request an alive check from the DoIP entity."""
        resp = self._doip_client.request_alive_check()
        return AliveCheckResponse(source_address=resp.source_address)

    @export
    @validate_call(validate_return=True)
    def diagnostic_power_mode(self) -> DiagnosticPowerModeResponse:
        """Request the diagnostic power mode."""
        resp = self._doip_client.request_diagnostic_power_mode()
        return DiagnosticPowerModeResponse(
            diagnostic_power_mode=resp.diagnostic_power_mode,
        )

    @export
    @validate_call(validate_return=True)
    def request_vehicle_identification(
        self, vin: str | None = None, eid: str | None = None
    ) -> VehicleIdentificationResponse:
        """Request vehicle identification, optionally filtered by VIN or EID."""
        kwargs = {}
        if vin is not None:
            kwargs["vin"] = vin
        if eid is not None:
            kwargs["eid"] = eid
        resp = self._doip_client.request_vehicle_identification(**kwargs)
        return VehicleIdentificationResponse(
            vin=resp.vin.decode() if isinstance(resp.vin, (bytes, bytearray)) else resp.vin,
            logical_address=resp.logical_address,
            eid=resp.eid.hex() if isinstance(resp.eid, (bytes, bytearray)) else resp.eid,
            gid=resp.gid.hex() if isinstance(resp.gid, (bytes, bytearray)) else resp.gid,
            further_action=resp.further_action,
            sync_status=getattr(resp, "sync_status", None),
        )

    @export
    @validate_call(validate_return=True)
    def routing_activation(self, activation_type: int = 0) -> RoutingActivationResponse:
        """Request routing activation for the given activation type."""
        resp = self._doip_client.request_activation(activation_type)
        return RoutingActivationResponse(
            client_logical_address=resp.client_logical_address,
            logical_address=resp.logical_address,
            response_code=resp.response_code,
            vm_specific=getattr(resp, "vm_specific", None),
        )

    @export
    @validate_call(validate_return=True)
    def send_diagnostic(self, payload: DiagnosticPayload) -> None:
        """Send a raw diagnostic payload to the ECU."""
        self._doip_client.send_diagnostic(bytes.fromhex(payload.data))

    @export
    @validate_call(validate_return=True)
    def receive_diagnostic(self, timeout: float = 2.0) -> DiagnosticPayload:
        """Receive a raw diagnostic response from the ECU."""
        raw = bytes(self._doip_client.receive_diagnostic(timeout=timeout))
        return DiagnosticPayload(data=raw.hex())

    @export
    @validate_call(validate_return=True)
    def reconnect(self, close_delay: float = 2.0) -> None:
        """Reconnect to the DoIP entity (e.g. after ECU reset)."""
        self._doip_client.reconnect(close_delay=close_delay)

    @export
    @validate_call(validate_return=True)
    def close_connection(self) -> None:
        """Close the DoIP connection."""
        self._doip_client.close()
