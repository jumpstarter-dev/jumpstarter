from __future__ import annotations

import logging

from pydantic import validate_call
from udsoncan.exceptions import NegativeResponseException
from udsoncan.services import (
    DiagnosticSessionControl,
    ECUReset,
)

from .common import (
    AuthenticationResponse,
    DidValue,
    DtcInfo,
    FileTransferResponse,
    RoutineControlResponse,
    SecuritySeedResponse,
    UdsResetType,
    UdsResponse,
    UdsSessionType,
)
from jumpstarter.driver import export

logger = logging.getLogger(__name__)

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
        except NegativeResponseException as e:
            logger.warning("ReadDataByIdentifier NRC 0x%02X (%s)", e.response.code, e.response.code_name)
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
        except NegativeResponseException as e:
            logger.warning("ReadDTCByStatusMask NRC 0x%02X (%s)", e.response.code, e.response.code_name)
            return []

    # -- RoutineControl (0x31) ------------------------------------------------

    @export
    @validate_call(validate_return=True)
    def start_routine(self, routine_id: int, data_hex: str = "") -> RoutineControlResponse:
        """Start a routine on the ECU."""
        data = bytes.fromhex(data_hex) if data_hex else None
        try:
            resp = self._uds_client.start_routine(routine_id, data)
            return RoutineControlResponse(
                routine_id=resp.service_data.routine_id_echo,
                control_type="startRoutine",
                success=resp.positive,
                status_record=resp.service_data.routine_status_record.hex()
                if resp.service_data.routine_status_record else None,
            )
        except NegativeResponseException as e:
            return RoutineControlResponse(
                routine_id=routine_id, control_type="startRoutine", success=False,
                nrc=e.response.code, nrc_name=e.response.code_name,
            )

    @export
    @validate_call(validate_return=True)
    def stop_routine(self, routine_id: int, data_hex: str = "") -> RoutineControlResponse:
        """Stop a running routine on the ECU."""
        data = bytes.fromhex(data_hex) if data_hex else None
        try:
            resp = self._uds_client.stop_routine(routine_id, data)
            return RoutineControlResponse(
                routine_id=resp.service_data.routine_id_echo,
                control_type="stopRoutine",
                success=resp.positive,
                status_record=resp.service_data.routine_status_record.hex()
                if resp.service_data.routine_status_record else None,
            )
        except NegativeResponseException as e:
            return RoutineControlResponse(
                routine_id=routine_id, control_type="stopRoutine", success=False,
                nrc=e.response.code, nrc_name=e.response.code_name,
            )

    @export
    @validate_call(validate_return=True)
    def get_routine_result(self, routine_id: int, data_hex: str = "") -> RoutineControlResponse:
        """Get the result of a routine on the ECU."""
        data = bytes.fromhex(data_hex) if data_hex else None
        try:
            resp = self._uds_client.get_routine_result(routine_id, data)
            return RoutineControlResponse(
                routine_id=resp.service_data.routine_id_echo,
                control_type="requestRoutineResults",
                success=resp.positive,
                status_record=resp.service_data.routine_status_record.hex()
                if resp.service_data.routine_status_record else None,
            )
        except NegativeResponseException as e:
            return RoutineControlResponse(
                routine_id=routine_id, control_type="requestRoutineResults", success=False,
                nrc=e.response.code, nrc_name=e.response.code_name,
            )

    # -- Authentication (0x29) ------------------------------------------------

    @export
    @validate_call(validate_return=True)
    def authentication(
        self,
        authentication_task: int,
        communication_configuration: int | None = None,
        certificate_client_hex: str = "",
        challenge_client_hex: str = "",
        algorithm_indicator_hex: str = "",
        proof_of_ownership_client_hex: str = "",
    ) -> AuthenticationResponse:
        """Send an Authentication request (ISO-14229-1:2020)."""
        kwargs: dict = {"authentication_task": authentication_task}
        if communication_configuration is not None:
            kwargs["communication_configuration"] = communication_configuration
        if certificate_client_hex:
            kwargs["certificate_client"] = bytes.fromhex(certificate_client_hex)
        if challenge_client_hex:
            kwargs["challenge_client"] = bytes.fromhex(challenge_client_hex)
        if algorithm_indicator_hex:
            kwargs["algorithm_indicator"] = bytes.fromhex(algorithm_indicator_hex)
        if proof_of_ownership_client_hex:
            kwargs["proof_of_ownership_client"] = bytes.fromhex(proof_of_ownership_client_hex)

        try:
            resp = self._uds_client.authentication(**kwargs)
            sd = resp.service_data
            return AuthenticationResponse(
                authentication_task=sd.authentication_task_echo,
                return_value=sd.return_value,
                success=resp.positive,
                challenge_server=sd.challenge_server.hex() if sd.challenge_server else None,
                certificate_server=sd.certificate_server.hex() if sd.certificate_server else None,
                proof_of_ownership_server=sd.proof_of_ownership_server.hex() if sd.proof_of_ownership_server else None,
                session_key_info=sd.session_key_info.hex() if sd.session_key_info else None,
                algorithm_indicator=sd.algorithm_indicator.hex() if sd.algorithm_indicator else None,
                needed_additional_parameter=(
                    sd.needed_additional_parameter.hex() if sd.needed_additional_parameter else None
                ),
            )
        except NegativeResponseException as e:
            return AuthenticationResponse(
                authentication_task=authentication_task, return_value=0, success=False,
                nrc=e.response.code, nrc_name=e.response.code_name,
            )

    # -- RequestFileTransfer (0x38) -------------------------------------------

    @export
    @validate_call(validate_return=True)
    def request_file_transfer(
        self, moop: int, path: str, filesize: int | None = None
    ) -> FileTransferResponse:
        """Request a file operation on the ECU (add, read, delete, readdir)."""
        from udsoncan import Filesize as UdsFilesize

        kwargs: dict = {"moop": moop, "path": path}
        if filesize is not None:
            kwargs["filesize"] = UdsFilesize(uncompressed=filesize, compressed=filesize)

        try:
            resp = self._uds_client.request_file_transfer(**kwargs)
            sd = resp.service_data
            return FileTransferResponse(
                moop=sd.moop_echo,
                success=resp.positive,
                max_length=sd.max_length,
                filesize_uncompressed=sd.filesize.uncompressed if sd.filesize else None,
                filesize_compressed=sd.filesize.compressed if sd.filesize else None,
                dirinfo_length=sd.dirinfo_length,
            )
        except NegativeResponseException as e:
            return FileTransferResponse(
                moop=moop, success=False,
                nrc=e.response.code, nrc_name=e.response.code_name,
            )
