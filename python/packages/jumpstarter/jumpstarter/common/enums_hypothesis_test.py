from hypothesis import given, settings
from hypothesis import strategies as st

from .enums import ExporterStatus, LogSource


class TestExporterStatusRoundtrip:
    @given(status=st.sampled_from(list(ExporterStatus)))
    @settings(max_examples=50)
    def test_from_proto_to_proto_roundtrip(self, status: ExporterStatus) -> None:
        assert ExporterStatus.from_proto(status.to_proto()) == status

    @given(status=st.sampled_from(list(ExporterStatus)))
    @settings(max_examples=50)
    def test_to_proto_returns_int(self, status: ExporterStatus) -> None:
        assert isinstance(status.to_proto(), int)

    @given(status=st.sampled_from(list(ExporterStatus)))
    @settings(max_examples=50)
    def test_str_is_name(self, status: ExporterStatus) -> None:
        assert str(status) == status.name

    @given(status=st.sampled_from(list(ExporterStatus)))
    @settings(max_examples=50)
    def test_value_equals_proto(self, status: ExporterStatus) -> None:
        assert status.value == status.to_proto()


class TestLogSourceRoundtrip:
    @given(source=st.sampled_from(list(LogSource)))
    @settings(max_examples=50)
    def test_from_proto_to_proto_roundtrip(self, source: LogSource) -> None:
        assert LogSource.from_proto(source.to_proto()) == source

    @given(source=st.sampled_from(list(LogSource)))
    @settings(max_examples=50)
    def test_to_proto_returns_int(self, source: LogSource) -> None:
        assert isinstance(source.to_proto(), int)

    @given(source=st.sampled_from(list(LogSource)))
    @settings(max_examples=50)
    def test_str_is_name(self, source: LogSource) -> None:
        assert str(source) == source.name

    @given(source=st.sampled_from(list(LogSource)))
    @settings(max_examples=50)
    def test_value_equals_proto(self, source: LogSource) -> None:
        assert source.value == source.to_proto()


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
