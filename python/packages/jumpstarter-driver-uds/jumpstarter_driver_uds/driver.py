from __future__ import annotations

from pydantic import validate_call
from udsoncan.exceptions import NegativeResponseException
from udsoncan.services import DiagnosticSessionControl, ECUReset

from .common import (
    DidValue,
    DtcInfo,
    SecuritySeedResponse,
    UdsResetType,
    UdsResponse,
    UdsSessionType,
)
from jumpstarter.driver import export

_SESSION_MAP = {
    UdsSessionType.DEFAULT: DiagnosticSessionControl.Session.defaultSession,
    UdsSessionType.PROGRAMMING: DiagnosticSessionControl.Session.programmingSession,
    UdsSessionType.EXTENDED: DiagnosticSessionControl.Session.extendedDiagnosticSession,
    UdsSessionType.SAFETY: DiagnosticSessionControl.Session.safetySystemDiagnosticSession,
}

_RESET_MAP = {
    UdsResetType.HARD: ECUReset.ResetType.hardReset,
    UdsResetType.KEY_OFF_ON: ECUReset.ResetType.keyOffOnReset,
    UdsResetType.SOFT: ECUReset.ResetType.softReset,
}


def _build_uds_response(service: str, response) -> UdsResponse:
    return UdsResponse(
        service=service,
        success=response.positive,
        data=response.data.hex() if hasattr(response, "data") and response.data else None,
    )


def _build_nrc_response(service: str, exc: NegativeResponseException) -> UdsResponse:
    return UdsResponse(
        service=service,
        success=False,
        nrc=exc.response.code,
        nrc_name=exc.response.code_name,
    )


class UdsInterface:
    """Base interface for UDS (Unified Diagnostic Services) drivers.

    Concrete subclasses must initialise ``self._uds_client`` (a
    ``udsoncan.client.Client``) in their ``__post_init__``.
    """

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_uds.client.UdsClient"

    @export
    @validate_call(validate_return=True)
    def change_session(self, session: UdsSessionType) -> UdsResponse:
        """Change the UDS diagnostic session."""
        try:
            resp = self._uds_client.change_session(_SESSION_MAP[session])
            return _build_uds_response("DiagnosticSessionControl", resp)
        except NegativeResponseException as e:
            return _build_nrc_response("DiagnosticSessionControl", e)

    @export
    @validate_call(validate_return=True)
    def ecu_reset(self, reset_type: UdsResetType) -> UdsResponse:
        """Reset the ECU."""
        try:
            resp = self._uds_client.ecu_reset(_RESET_MAP[reset_type])
            return _build_uds_response("ECUReset", resp)
        except NegativeResponseException as e:
            return _build_nrc_response("ECUReset", e)

    @export
    @validate_call(validate_return=True)
    def tester_present(self) -> None:
        """Send a TesterPresent request to keep the session alive."""
        self._uds_client.tester_present()

    @export
    @validate_call(validate_return=True)
    def read_data_by_identifier(self, did_list: list[int]) -> list[DidValue]:
        """Read one or more Data Identifiers from the ECU."""
        try:
            resp = self._uds_client.read_data_by_identifier(did_list)
            values = resp.service_data.values if hasattr(resp, "service_data") else {}
            return [
                DidValue(did=did, value=v.hex() if isinstance(v, (bytes, bytearray)) else v)
                for did, v in values.items()
            ]
        except NegativeResponseException:
            return []

    @export
    @validate_call(validate_return=True)
    def write_data_by_identifier(self, did: int, value_hex: str) -> UdsResponse:
        """Write a Data Identifier value to the ECU (value as hex string)."""
        try:
            resp = self._uds_client.write_data_by_identifier(did, bytes.fromhex(value_hex))
            return _build_uds_response("WriteDataByIdentifier", resp)
        except NegativeResponseException as e:
            return _build_nrc_response("WriteDataByIdentifier", e)

    @export
    @validate_call(validate_return=True)
    def request_seed(self, level: int) -> SecuritySeedResponse:
        """Request a security access seed for the given level."""
        try:
            resp = self._uds_client.request_seed(level)
            seed_bytes = resp.service_data.seed
            return SecuritySeedResponse(seed=seed_bytes.hex() if isinstance(seed_bytes, bytes) else str(seed_bytes))
        except NegativeResponseException as e:
            return SecuritySeedResponse(
                seed="",
                success=False,
                nrc=e.response.code,
                nrc_name=e.response.code_name,
            )

    @export
    @validate_call(validate_return=True)
    def send_key(self, level: int, key_hex: str) -> UdsResponse:
        """Send a security access key for the given level (key as hex string)."""
        try:
            resp = self._uds_client.send_key(level, bytes.fromhex(key_hex))
            return _build_uds_response("SecurityAccess", resp)
        except NegativeResponseException as e:
            return _build_nrc_response("SecurityAccess", e)

    @export
    @validate_call(validate_return=True)
    def clear_dtc(self, group: int = 0xFFFFFF) -> UdsResponse:
        """Clear diagnostic trouble codes."""
        try:
            resp = self._uds_client.clear_dtc(group)
            return _build_uds_response("ClearDiagnosticInformation", resp)
        except NegativeResponseException as e:
            return _build_nrc_response("ClearDiagnosticInformation", e)

    @export
    @validate_call(validate_return=True)
    def read_dtc_by_status_mask(self, mask: int = 0xFF) -> list[DtcInfo]:
        """Read DTCs matching the given status mask."""
        try:
            resp = self._uds_client.get_dtc_by_status_mask(mask)
            dtcs = resp.service_data.dtcs if hasattr(resp, "service_data") else []
            return [
                DtcInfo(
                    dtc_id=dtc.id,
                    status=dtc.status.get_byte_as_int(),
                    severity=dtc.severity.get_byte_as_int() if hasattr(dtc.severity, "get_byte_as_int") else None,
                )
                for dtc in dtcs
            ]
        except NegativeResponseException:
            return []
