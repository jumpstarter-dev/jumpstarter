from __future__ import annotations

from dataclasses import dataclass

from .common import (
    AliveCheckResponse,
    DiagnosticPayload,
    DiagnosticPowerModeResponse,
    EntityStatusResponse,
    RoutingActivationResponse,
    VehicleIdentificationResponse,
)
from jumpstarter.client import DriverClient


@dataclass(kw_only=True)
class DoIPDriverClient(DriverClient):
    """
    Client interface for raw DoIP operations.

    Provides methods for vehicle discovery, entity status checks,
    alive checks, and raw diagnostic message exchange.
    """

    def entity_status(self) -> EntityStatusResponse:
        """Request the DoIP entity status."""
        return EntityStatusResponse.model_validate(self.call("entity_status"))

    def alive_check(self) -> AliveCheckResponse:
        """Request an alive check from the DoIP entity."""
        return AliveCheckResponse.model_validate(self.call("alive_check"))

    def diagnostic_power_mode(self) -> DiagnosticPowerModeResponse:
        """Request the diagnostic power mode."""
        return DiagnosticPowerModeResponse.model_validate(self.call("diagnostic_power_mode"))

    def request_vehicle_identification(
        self, vin: str | None = None, eid: str | None = None
    ) -> VehicleIdentificationResponse:
        """Request vehicle identification, optionally filtered by VIN or EID."""
        return VehicleIdentificationResponse.model_validate(self.call("request_vehicle_identification", vin, eid))

    def routing_activation(self, activation_type: int = 0) -> RoutingActivationResponse:
        """Request routing activation for the given activation type."""
        return RoutingActivationResponse.model_validate(self.call("routing_activation", activation_type))

    def send_diagnostic(self, payload: bytes) -> None:
        """Send a raw diagnostic payload to the ECU."""
        msg = DiagnosticPayload(data=payload.hex())
        self.call("send_diagnostic", msg)

    def receive_diagnostic(self, timeout: float = 2.0) -> bytes:
        """Receive a raw diagnostic response from the ECU."""
        result = DiagnosticPayload.model_validate(self.call("receive_diagnostic", timeout))
        return bytes.fromhex(result.data)

    def reconnect(self, close_delay: float = 2.0) -> None:
        """Reconnect to the DoIP entity (e.g. after ECU reset)."""
        self.call("reconnect", close_delay)

    def close_connection(self) -> None:
        """Close the DoIP connection."""
        self.call("close_connection")
