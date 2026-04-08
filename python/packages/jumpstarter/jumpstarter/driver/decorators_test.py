"""Tests for @export and @exportstream decorators (Phase 2)."""

import inspect
from collections.abc import AsyncGenerator, Generator

import pytest

from . import export, exportstream
from .decorators import (
    MARKER_DRIVERCALL,
    MARKER_MAGIC,
    MARKER_STREAMCALL,
    MARKER_STREAMING_DRIVERCALL,
    MARKER_TYPE_INFO,
    CallType,
    ExportedMethodInfo,
)


# ---------------------------------------------------------------------------
# Fixtures: decorated methods with proper type annotations
# ---------------------------------------------------------------------------

class Functions:
    @export
    def function(self) -> None:
        pass

    @export
    async def asyncfunction(self) -> None:
        pass

    @export
    def generator(self) -> Generator[int, None, None]:
        yield 1

    @export
    async def asyncgenerator(self) -> AsyncGenerator[int, None]:
        yield 1


# ---------------------------------------------------------------------------
# Original marker tests (updated for type-annotated signatures)
# ---------------------------------------------------------------------------

class TestExportMarkers:
    """Verify that @export still sets the original marker attributes."""

    def test_function_marker(self):
        functions = Functions()
        assert getattr(functions.function, MARKER_DRIVERCALL) == MARKER_MAGIC

    def test_async_function_marker(self):
        functions = Functions()
        assert getattr(functions.asyncfunction, MARKER_DRIVERCALL) == MARKER_MAGIC

    def test_generator_marker(self):
        functions = Functions()
        assert getattr(functions.generator, MARKER_STREAMING_DRIVERCALL) == MARKER_MAGIC

    def test_async_generator_marker(self):
        functions = Functions()
        assert getattr(functions.asyncgenerator, MARKER_STREAMING_DRIVERCALL) == MARKER_MAGIC

    def test_export_rejects_non_callable(self):
        with pytest.raises(TypeError):
            export(None)


# ---------------------------------------------------------------------------
# Phase 2: ExportedMethodInfo capture
# ---------------------------------------------------------------------------

class TestExportedMethodInfoCapture:
    """Verify @export stores correct ExportedMethodInfo metadata."""

    def test_unary_method_info(self):
        """Simple unary method: no params, returns None."""
        functions = Functions()
        info: ExportedMethodInfo = getattr(functions.function, MARKER_TYPE_INFO)
        assert info.name == "function"
        assert info.call_type == CallType.UNARY
        assert info.params == []
        assert info.return_type is None  # -> None

    def test_async_unary_method_info(self):
        """Async unary method: no params, returns None."""
        functions = Functions()
        info: ExportedMethodInfo = getattr(functions.asyncfunction, MARKER_TYPE_INFO)
        assert info.name == "asyncfunction"
        assert info.call_type == CallType.UNARY
        assert info.params == []

    def test_generator_is_server_streaming(self):
        """Sync generator → SERVER_STREAMING."""
        functions = Functions()
        info: ExportedMethodInfo = getattr(functions.generator, MARKER_TYPE_INFO)
        assert info.call_type == CallType.SERVER_STREAMING

    def test_async_generator_is_server_streaming(self):
        """Async generator → SERVER_STREAMING."""
        functions = Functions()
        info: ExportedMethodInfo = getattr(functions.asyncgenerator, MARKER_TYPE_INFO)
        assert info.call_type == CallType.SERVER_STREAMING

    def test_method_with_params(self):
        """Method with typed parameters captures them correctly."""

        class Svc:
            @export
            async def set_voltage(self, voltage: float, channel: int = 0) -> None:
                pass

        info: ExportedMethodInfo = getattr(Svc.set_voltage, MARKER_TYPE_INFO)
        assert info.name == "set_voltage"
        assert info.call_type == CallType.UNARY
        assert len(info.params) == 2
        # First param: (name, annotation, default)
        assert info.params[0][0] == "voltage"
        assert info.params[0][1] is float
        assert info.params[0][2] is inspect.Parameter.empty  # no default
        # Second param with default
        assert info.params[1][0] == "channel"
        assert info.params[1][1] is int
        assert info.params[1][2] == 0

    def test_return_type_captured(self):
        """Return type annotation is stored in ExportedMethodInfo."""

        class Svc:
            @export
            async def get_temp(self) -> float:
                return 25.0

        info: ExportedMethodInfo = getattr(Svc.get_temp, MARKER_TYPE_INFO)
        assert info.return_type is float

    def test_bidi_streaming_detection(self):
        """A method with AsyncGenerator param AND return is BIDI_STREAMING."""

        class Svc:
            @export
            async def transfer(
                self, data: AsyncGenerator[bytes, None]
            ) -> AsyncGenerator[bytes, None]:
                yield b""

        info: ExportedMethodInfo = getattr(Svc.transfer, MARKER_TYPE_INFO)
        assert info.call_type == CallType.BIDI_STREAMING

    def test_client_streaming_detection(self):
        """AsyncGenerator parameter without generator return → CLIENT_STREAMING."""

        class Svc:
            @export
            async def upload(self, data: AsyncGenerator[bytes, None]) -> int:
                return 0

        info: ExportedMethodInfo = getattr(Svc.upload, MARKER_TYPE_INFO)
        assert info.call_type == CallType.CLIENT_STREAMING


# ---------------------------------------------------------------------------
# Phase 2: Type annotation enforcement
# ---------------------------------------------------------------------------

class TestTypeAnnotationEnforcement:
    """Verify @export rejects methods missing type annotations."""

    def test_missing_return_type_raises(self):
        """@export must reject methods without return type annotation."""
        with pytest.raises(TypeError, match="return type annotation"):

            class Bad:
                @export
                def method(self):
                    pass

    def test_missing_param_annotation_raises(self):
        """@export must reject parameters without type annotations."""
        with pytest.raises(TypeError, match="must have a type annotation"):

            class Bad:
                @export
                def method(self, x) -> None:
                    pass

    def test_self_does_not_need_annotation(self):
        """'self' parameter should be exempt from annotation requirement."""

        class Good:
            @export
            def method(self) -> None:
                pass

        # Should not raise
        assert hasattr(Good.method, MARKER_TYPE_INFO)

    def test_multiple_unannotated_params(self):
        """First unannotated param triggers the error."""
        with pytest.raises(TypeError, match="parameter 'a'"):

            class Bad:
                @export
                def method(self, a, b) -> None:
                    pass


# ---------------------------------------------------------------------------
# @exportstream tests
# ---------------------------------------------------------------------------

class TestExportStream:
    """Tests for the @exportstream decorator."""

    def test_exportstream_sets_marker(self):
        class Svc:
            @exportstream
            def connect(self) -> None:
                pass

        assert getattr(Svc.connect, MARKER_STREAMCALL) == MARKER_MAGIC

    def test_exportstream_always_bidi(self):
        """@exportstream methods are always BIDI_STREAMING."""

        class Svc:
            @exportstream
            def connect(self) -> None:
                pass

        info: ExportedMethodInfo = getattr(Svc.connect, MARKER_TYPE_INFO)
        assert info.call_type == CallType.BIDI_STREAMING

    def test_exportstream_captures_type_info(self):
        """@exportstream captures ExportedMethodInfo like @export."""

        class Svc:
            @exportstream
            def connect(self, port: int) -> None:
                pass

        info: ExportedMethodInfo = getattr(Svc.connect, MARKER_TYPE_INFO)
        assert info.name == "connect"
        assert len(info.params) == 1
        assert info.params[0][0] == "port"
        assert info.params[0][1] is int

    def test_exportstream_rejects_missing_return_type(self):
        with pytest.raises(TypeError, match="return type annotation"):

            class Bad:
                @exportstream
                def connect(self):
                    pass

    def test_exportstream_rejects_missing_param_annotation(self):
        with pytest.raises(TypeError, match="must have a type annotation"):

            class Bad:
                @exportstream
                def connect(self, port) -> None:
                    pass


# ---------------------------------------------------------------------------
# Mixed @export / @exportstream interface
# ---------------------------------------------------------------------------

class TestMixedInterface:
    """Verify a class can mix @export and @exportstream methods."""

    def test_mixed_decorators(self):
        class Svc:
            @export
            async def on(self) -> None:
                pass

            @export
            async def off(self) -> None:
                pass

            @exportstream
            def stream(self) -> None:
                pass

        # export methods
        assert hasattr(Svc.on, MARKER_DRIVERCALL)
        assert hasattr(Svc.off, MARKER_DRIVERCALL)
        # exportstream method
        assert hasattr(Svc.stream, MARKER_STREAMCALL)
        # All have type info
        for method in (Svc.on, Svc.off, Svc.stream):
            assert hasattr(method, MARKER_TYPE_INFO)


# ---------------------------------------------------------------------------
# ExportedMethodInfo dataclass properties
# ---------------------------------------------------------------------------

class TestExportedMethodInfoDataclass:
    """Test ExportedMethodInfo is frozen and well-formed."""

    def test_frozen(self):
        """ExportedMethodInfo should be immutable (frozen dataclass)."""
        info = ExportedMethodInfo(
            name="test", call_type=CallType.UNARY, params=[], return_type=None
        )
        with pytest.raises(AttributeError):
            info.name = "changed"

    def test_call_type_enum_values(self):
        """Verify all expected CallType values exist."""
        assert CallType.UNARY.value == "unary"
        assert CallType.SERVER_STREAMING.value == "server_streaming"
        assert CallType.CLIENT_STREAMING.value == "client_streaming"
        assert CallType.BIDI_STREAMING.value == "bidi_streaming"
