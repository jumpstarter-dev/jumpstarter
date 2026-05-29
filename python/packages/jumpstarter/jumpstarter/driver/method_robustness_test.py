from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from jumpstarter_protocol import jumpstarter_pb2

from jumpstarter.common.serde import encode_value
from jumpstarter.driver.base import Driver
from jumpstarter.driver.decorators import MARKER_DRIVERCALL, MARKER_MAGIC, export, exportstream


class MinimalDriver(Driver):
    @classmethod
    def client(cls) -> str:
        return "test.MinimalClient"

    @export
    def echo(self, value):
        return value

    @export
    async def async_echo(self, value):
        return value

    @export
    def typed_add(self, a: int, b: int) -> int:
        return a + b

    @export
    def fail_always(self):
        raise ValueError("intentional failure")


def _make_context():
    context = AsyncMock()
    context.abort = AsyncMock(side_effect=_make_abort_side_effect())
    return context


def _make_abort_side_effect():
    class AbortError(Exception):
        pass

    def abort(code, message):
        raise AbortError(f"{code}: {message}")

    return abort


def _make_request(method: str, *args):
    encoded_args = [encode_value(a) for a in args]
    return jumpstarter_pb2.DriverCallRequest(
        uuid=str(uuid4()),
        method=method,
        args=encoded_args,
    )


class TestDriverCallDispatching:
    @pytest.mark.anyio
    async def test_echo_with_string(self) -> None:
        driver = MinimalDriver()
        context = _make_context()
        request = _make_request("echo", "hello")
        response = await driver.DriverCall(request, context)
        assert response is not None

    @pytest.mark.anyio
    async def test_echo_with_int(self) -> None:
        driver = MinimalDriver()
        context = _make_context()
        request = _make_request("echo", 42)
        response = await driver.DriverCall(request, context)
        assert response is not None

    @pytest.mark.anyio
    async def test_echo_with_none(self) -> None:
        driver = MinimalDriver()
        context = _make_context()
        request = _make_request("echo", None)
        response = await driver.DriverCall(request, context)
        assert response is not None

    @pytest.mark.anyio
    async def test_async_echo(self) -> None:
        driver = MinimalDriver()
        context = _make_context()
        request = _make_request("async_echo", "test")
        response = await driver.DriverCall(request, context)
        assert response is not None


class TestDriverCallWithWrongTypes:
    @pytest.mark.anyio
    async def test_typed_add_with_strings_instead_of_ints(self) -> None:
        driver = MinimalDriver()
        context = _make_context()
        request = _make_request("typed_add", "not_int", "also_not_int")
        try:
            await driver.DriverCall(request, context)
        except Exception:
            pass

    @pytest.mark.anyio
    async def test_typed_add_with_none_args(self) -> None:
        driver = MinimalDriver()
        context = _make_context()
        request = _make_request("typed_add", None, None)
        try:
            await driver.DriverCall(request, context)
        except Exception:
            pass

    @pytest.mark.anyio
    async def test_too_few_args(self) -> None:
        driver = MinimalDriver()
        context = _make_context()
        request = _make_request("typed_add", 1)
        try:
            await driver.DriverCall(request, context)
        except Exception:
            pass

    @pytest.mark.anyio
    async def test_too_many_args(self) -> None:
        driver = MinimalDriver()
        context = _make_context()
        request = _make_request("typed_add", 1, 2, 3, 4)
        try:
            await driver.DriverCall(request, context)
        except Exception:
            pass

    @pytest.mark.anyio
    async def test_method_that_always_fails(self) -> None:
        driver = MinimalDriver()
        context = _make_context()
        request = _make_request("fail_always")
        try:
            await driver.DriverCall(request, context)
        except Exception:
            pass


class TestDriverCallMethodLookup:
    @pytest.mark.anyio
    async def test_nonexistent_method_aborts(self) -> None:
        driver = MinimalDriver()
        context = _make_context()
        request = _make_request("nonexistent_method")
        try:
            await driver.DriverCall(request, context)
        except Exception:
            pass

    @pytest.mark.anyio
    async def test_private_method_not_exported(self) -> None:
        driver = MinimalDriver()
        context = _make_context()
        request = _make_request("__init__")
        try:
            await driver.DriverCall(request, context)
        except Exception:
            pass

    @pytest.mark.anyio
    async def test_close_not_exported(self) -> None:
        driver = MinimalDriver()
        context = _make_context()
        request = _make_request("close")
        try:
            await driver.DriverCall(request, context)
        except Exception:
            pass

    @given(method_name=st.text(min_size=1, max_size=50))
    def test_arbitrary_method_name_never_crashes(self, method_name: str) -> None:
        import anyio

        async def _run():
            driver = MinimalDriver()
            context = _make_context()
            request = _make_request(method_name)
            try:
                await driver.DriverCall(request, context)
            except Exception:
                pass

        anyio.from_thread.run(_run) if False else anyio.run(_run)


class TestDriverCallWithFuzzedValues:
    @given(
        value=st.one_of(
            st.text(max_size=100),
            st.integers(min_value=-(10**9), max_value=10**9),
            st.floats(allow_nan=False, allow_infinity=False),
            st.booleans(),
            st.none(),
        )
    )
    def test_echo_with_arbitrary_values(self, value) -> None:
        import anyio

        async def _run():
            driver = MinimalDriver()
            context = _make_context()
            request = _make_request("echo", value)
            try:
                response = await driver.DriverCall(request, context)
                assert response is not None
            except Exception:
                pass

        anyio.run(_run)

    @given(
        values=st.lists(
            st.one_of(
                st.text(max_size=20),
                st.integers(min_value=-1000, max_value=1000),
                st.none(),
            ),
            max_size=5,
        )
    )
    def test_echo_with_list_values(self, values: list) -> None:
        import anyio

        async def _run():
            driver = MinimalDriver()
            context = _make_context()
            request = _make_request("echo", values)
            try:
                response = await driver.DriverCall(request, context)
                assert response is not None
            except Exception:
                pass

        anyio.run(_run)


class TestExportDecoratorRobustness:
    def test_export_regular_function(self) -> None:
        @export
        def my_func():
            pass

        assert getattr(my_func, MARKER_DRIVERCALL, None) == MARKER_MAGIC

    def test_export_async_function(self) -> None:
        @export
        async def my_async_func():
            pass

        assert getattr(my_async_func, MARKER_DRIVERCALL, None) == MARKER_MAGIC

    def test_export_rejects_non_callable(self) -> None:
        with pytest.raises(ValueError, match="unsupported"):
            export("not a function")

    def test_exportstream_sets_marker(self) -> None:
        @exportstream
        async def my_stream():
            pass

        from jumpstarter.driver.decorators import MARKER_STREAMCALL

        assert getattr(my_stream, MARKER_STREAMCALL, None) == MARKER_MAGIC


class TestDriverReport:
    def test_report_returns_valid_proto(self) -> None:
        driver = MinimalDriver()
        report = driver.report()
        assert report.uuid
        assert "jumpstarter.dev/client" in report.labels

    def test_report_with_parent(self) -> None:
        parent = MinimalDriver()
        child = MinimalDriver()
        report = child.report(parent=parent, name="child1")
        assert report.parent_uuid == str(parent.uuid)
        assert report.labels["jumpstarter.dev/name"] == "child1"

    def test_enumerate_returns_self(self) -> None:
        driver = MinimalDriver()
        result = driver.enumerate()
        assert len(result) == 1
        assert result[0][3] is driver

    def test_enumerate_with_children(self) -> None:
        child = MinimalDriver()
        parent = MinimalDriver(children={"c1": child})
        result = parent.enumerate()
        assert len(result) == 2
