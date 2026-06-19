"""Tests for the ExporterStatus / LogSource enums.

The integer values are pinned explicitly here (they are the proto ``jumpstarter.v1`` wire
contract — the Rust core sends them across FFI), independent of any protobuf import.
"""

from jumpstarter.common.enums import ExporterStatus, LogSource

EXPORTER_STATUS_VALUES = {
    ExporterStatus.UNSPECIFIED: 0,
    ExporterStatus.OFFLINE: 1,
    ExporterStatus.AVAILABLE: 2,
    ExporterStatus.BEFORE_LEASE_HOOK: 3,
    ExporterStatus.LEASE_READY: 4,
    ExporterStatus.AFTER_LEASE_HOOK: 5,
    ExporterStatus.BEFORE_LEASE_HOOK_FAILED: 6,
    ExporterStatus.AFTER_LEASE_HOOK_FAILED: 7,
}

LOG_SOURCE_VALUES = {
    LogSource.UNSPECIFIED: 0,
    LogSource.DRIVER: 1,
    LogSource.BEFORE_LEASE_HOOK: 2,
    LogSource.AFTER_LEASE_HOOK: 3,
    LogSource.SYSTEM: 4,
}


class TestExporterStatus:
    def test_values_match_proto_contract(self) -> None:
        for status, value in EXPORTER_STATUS_VALUES.items():
            assert status.to_proto() == value
            assert ExporterStatus.from_proto(value) == status

    def test_str_representation(self) -> None:
        for status in ExporterStatus:
            assert str(status) == status.name

    def test_roundtrip_conversion(self) -> None:
        for status in ExporterStatus:
            assert ExporterStatus.from_proto(status.to_proto()) == status


class TestLogSource:
    def test_values_match_proto_contract(self) -> None:
        for source, value in LOG_SOURCE_VALUES.items():
            assert source.to_proto() == value
            assert LogSource.from_proto(value) == source

    def test_str_representation(self) -> None:
        for source in LogSource:
            assert str(source) == source.name

    def test_roundtrip_conversion(self) -> None:
        for source in LogSource:
            assert LogSource.from_proto(source.to_proto()) == source
