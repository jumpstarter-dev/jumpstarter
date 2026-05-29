import pytest
from jumpstarter_protocol.jumpstarter.v1 import common_pb2

from .enums import ExporterStatus, LogSource


class TestExporterStatusRoundtrip:
    @pytest.mark.parametrize("status", list(ExporterStatus))
    def test_from_proto_to_proto_roundtrip(self, status: ExporterStatus) -> None:
        assert ExporterStatus.from_proto(status.to_proto()) == status

    @pytest.mark.parametrize("status", list(ExporterStatus))
    def test_to_proto_returns_int(self, status: ExporterStatus) -> None:
        assert isinstance(status.to_proto(), int)

    @pytest.mark.parametrize("status", list(ExporterStatus))
    def test_str_is_name(self, status: ExporterStatus) -> None:
        assert str(status) == status.name

    @pytest.mark.parametrize(
        ("status", "expected_proto"),
        [
            (ExporterStatus.UNSPECIFIED, common_pb2.EXPORTER_STATUS_UNSPECIFIED),
            (ExporterStatus.OFFLINE, common_pb2.EXPORTER_STATUS_OFFLINE),
            (ExporterStatus.AVAILABLE, common_pb2.EXPORTER_STATUS_AVAILABLE),
            (ExporterStatus.BEFORE_LEASE_HOOK, common_pb2.EXPORTER_STATUS_BEFORE_LEASE_HOOK),
            (ExporterStatus.LEASE_READY, common_pb2.EXPORTER_STATUS_LEASE_READY),
            (ExporterStatus.AFTER_LEASE_HOOK, common_pb2.EXPORTER_STATUS_AFTER_LEASE_HOOK),
            (ExporterStatus.BEFORE_LEASE_HOOK_FAILED, common_pb2.EXPORTER_STATUS_BEFORE_LEASE_HOOK_FAILED),
            (ExporterStatus.AFTER_LEASE_HOOK_FAILED, common_pb2.EXPORTER_STATUS_AFTER_LEASE_HOOK_FAILED),
        ],
    )
    def test_value_matches_protobuf_constant(self, status: ExporterStatus, expected_proto: int) -> None:
        assert status.to_proto() == expected_proto


class TestLogSourceRoundtrip:
    @pytest.mark.parametrize("source", list(LogSource))
    def test_from_proto_to_proto_roundtrip(self, source: LogSource) -> None:
        assert LogSource.from_proto(source.to_proto()) == source

    @pytest.mark.parametrize("source", list(LogSource))
    def test_to_proto_returns_int(self, source: LogSource) -> None:
        assert isinstance(source.to_proto(), int)

    @pytest.mark.parametrize("source", list(LogSource))
    def test_str_is_name(self, source: LogSource) -> None:
        assert str(source) == source.name

    @pytest.mark.parametrize(
        ("source", "expected_proto"),
        [
            (LogSource.UNSPECIFIED, common_pb2.LOG_SOURCE_UNSPECIFIED),
            (LogSource.DRIVER, common_pb2.LOG_SOURCE_DRIVER),
            (LogSource.BEFORE_LEASE_HOOK, common_pb2.LOG_SOURCE_BEFORE_LEASE_HOOK),
            (LogSource.AFTER_LEASE_HOOK, common_pb2.LOG_SOURCE_AFTER_LEASE_HOOK),
            (LogSource.SYSTEM, common_pb2.LOG_SOURCE_SYSTEM),
        ],
    )
    def test_value_matches_protobuf_constant(self, source: LogSource, expected_proto: int) -> None:
        assert source.to_proto() == expected_proto


class TestEnumCoverage:
    def test_exporter_status_has_at_least_one_member(self) -> None:
        assert len(list(ExporterStatus)) > 0

    def test_log_source_has_at_least_one_member(self) -> None:
        assert len(list(LogSource)) > 0

    def test_exporter_status_values_are_unique(self) -> None:
        values = [s.value for s in ExporterStatus]
        assert len(values) == len(set(values))

    def test_log_source_values_are_unique(self) -> None:
        values = [s.value for s in LogSource]
        assert len(values) == len(set(values))
