import pytest
from hypothesis import given
from hypothesis import strategies as st

from .decorators import (
    MARKER_DRIVERCALL,
    MARKER_MAGIC,
    MARKER_STREAMCALL,
    MARKER_STREAMING_DRIVERCALL,
    export,
    exportstream,
)


class TestExportDecorator:
    @given(invocation_count=st.integers(min_value=1, max_value=10))
    def test_export_marks_sync_function_idempotently(self, invocation_count: int) -> None:
        def my_func():
            pass

        for _ in range(invocation_count):
            export(my_func)
        assert getattr(my_func, MARKER_DRIVERCALL) == MARKER_MAGIC

    @given(invocation_count=st.integers(min_value=1, max_value=10))
    def test_export_marks_async_function_idempotently(self, invocation_count: int) -> None:
        async def my_func():
            pass

        for _ in range(invocation_count):
            export(my_func)
        assert getattr(my_func, MARKER_DRIVERCALL) == MARKER_MAGIC

    def test_export_marks_generator_function(self) -> None:
        def my_gen():
            yield

        decorated = export(my_gen)
        assert getattr(decorated, MARKER_STREAMING_DRIVERCALL) == MARKER_MAGIC

    def test_export_marks_async_generator_function(self) -> None:
        async def my_async_gen():
            yield

        decorated = export(my_async_gen)
        assert getattr(decorated, MARKER_STREAMING_DRIVERCALL) == MARKER_MAGIC

    def test_export_returns_same_function(self) -> None:
        def my_func():
            pass

        decorated = export(my_func)
        assert decorated is my_func

    @given(bad_value=st.one_of(st.text(min_size=0, max_size=20), st.integers(), st.none()))
    def test_export_rejects_non_callable(self, bad_value) -> None:
        with pytest.raises(ValueError, match="unsupported exported function"):
            export(bad_value)


class TestExportstreamDecorator:
    @given(invocation_count=st.integers(min_value=1, max_value=10))
    def test_exportstream_marks_function_idempotently(self, invocation_count: int) -> None:
        def my_func():
            pass

        for _ in range(invocation_count):
            exportstream(my_func)
        assert getattr(my_func, MARKER_STREAMCALL) == MARKER_MAGIC

    def test_exportstream_returns_same_function(self) -> None:
        def my_func():
            pass

        decorated = exportstream(my_func)
        assert decorated is my_func


class TestMarkerConstants:
    def test_marker_magic_is_consistent(self) -> None:
        assert MARKER_MAGIC == "07c9b9cc"

    def test_marker_names_are_distinct(self) -> None:
        markers = {MARKER_DRIVERCALL, MARKER_STREAMCALL, MARKER_STREAMING_DRIVERCALL}
        assert len(markers) == 3

    @given(marker=st.sampled_from([MARKER_DRIVERCALL, MARKER_STREAMCALL, MARKER_STREAMING_DRIVERCALL]))
    def test_all_markers_are_non_empty_strings(self, marker: str) -> None:
        assert isinstance(marker, str)
        assert len(marker) > 0
