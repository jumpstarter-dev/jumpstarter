from __future__ import annotations

from dataclasses import dataclass

from .common import (
    AuthenticationResponse,
    DidValue,
    DtcInfo,
    FileTransferResponse,
    RoutineControlResponse,
    SecuritySeedResponse,
    UdsResponse,
)
from jumpstarter.client import DriverClient


@dataclass(kw_only=True)
class UdsClient(DriverClient):
    """Client interface for UDS (Unified Diagnostic Services) operations.

    This client is shared by all UDS transport drivers (DoIP, CAN, etc.),
    providing a transport-agnostic interface for core UDS services.
    """

    def change_session(self, session: str) -> UdsResponse:
        """Change the UDS diagnostic session."""
        return UdsResponse.model_validate(self.call("change_session", session))

    def ecu_reset(self, reset_type: str) -> UdsResponse:
        """Reset the ECU."""
        return UdsResponse.model_validate(self.call("ecu_reset", reset_type))

    def tester_present(self) -> None:
        """Send a TesterPresent request to keep the session alive."""
        self.call("tester_present")

    def read_data_by_identifier(self, did_list: list[int]) -> list[DidValue]:
        """Read one or more Data Identifiers from the ECU."""
        result = self.call("read_data_by_identifier", did_list)
        return [DidValue.model_validate(v) for v in result]

    def write_data_by_identifier(self, did: int, value: bytes) -> UdsResponse:
        """Write a Data Identifier value to the ECU."""
        return UdsResponse.model_validate(self.call("write_data_by_identifier", did, value.hex()))

    def request_seed(self, level: int) -> SecuritySeedResponse:
        """Request a security access seed for the given level."""
        return SecuritySeedResponse.model_validate(self.call("request_seed", level))

    def send_key(self, level: int, key: bytes) -> UdsResponse:
        """Send a security access key for the given level."""
        return UdsResponse.model_validate(self.call("send_key", level, key.hex()))

    def clear_dtc(self, group: int = 0xFFFFFF) -> UdsResponse:
        """Clear diagnostic trouble codes."""
        return UdsResponse.model_validate(self.call("clear_dtc", group))

    def read_dtc_by_status_mask(self, mask: int = 0xFF) -> list[DtcInfo]:
        """Read DTCs matching the given status mask."""
        result = self.call("read_dtc_by_status_mask", mask)
        return [DtcInfo.model_validate(v) for v in result]

    def start_routine(self, routine_id: int, data: bytes | None = None) -> RoutineControlResponse:
        """Start a routine on the ECU."""
        return RoutineControlResponse.model_validate(
            self.call("start_routine", routine_id, data.hex() if data else "")
        )

    def stop_routine(self, routine_id: int, data: bytes | None = None) -> RoutineControlResponse:
        """Stop a running routine on the ECU."""
        return RoutineControlResponse.model_validate(
            self.call("stop_routine", routine_id, data.hex() if data else "")
        )

    def get_routine_result(self, routine_id: int, data: bytes | None = None) -> RoutineControlResponse:
        """Get the result of a routine on the ECU."""
        return RoutineControlResponse.model_validate(
            self.call("get_routine_result", routine_id, data.hex() if data else "")
        )

    def authentication(
        self,
        authentication_task: int,
        communication_configuration: int | None = None,
        certificate_client: bytes | None = None,
        challenge_client: bytes | None = None,
        algorithm_indicator: bytes | None = None,
        proof_of_ownership_client: bytes | None = None,
    ) -> AuthenticationResponse:
        """Send an Authentication request (ISO-14229-1:2020)."""
        return AuthenticationResponse.model_validate(
            self.call(
                "authentication",
                authentication_task,
                communication_configuration,
                certificate_client.hex() if certificate_client else "",
                challenge_client.hex() if challenge_client else "",
                algorithm_indicator.hex() if algorithm_indicator else "",
                proof_of_ownership_client.hex() if proof_of_ownership_client else "",
            )
        )

    def request_file_transfer(
        self, moop: int, path: str, filesize: int | None = None
    ) -> FileTransferResponse:
        """Request a file operation on the ECU."""
        return FileTransferResponse.model_validate(
            self.call("request_file_transfer", moop, path, filesize)
        )
