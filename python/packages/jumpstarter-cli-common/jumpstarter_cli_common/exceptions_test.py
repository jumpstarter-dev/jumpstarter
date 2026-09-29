import ssl
from json import JSONDecodeError
from unittest.mock import MagicMock

import click
import pytest

from jumpstarter_cli_common.exceptions import (
    async_handle_exceptions,
    handle_exceptions,
    handle_exceptions_with_reauthentication,
)

from jumpstarter.common.exceptions import ConnectionError as JmpConnectionError


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_handle_exceptions_maps_timeout_error() -> None:
    @handle_exceptions
    def timeout_fn():
        raise TimeoutError("flash operation exceeded timeout")

    with pytest.raises(click.ClickException, match="Operation timed out"):
        timeout_fn()


def test_handle_exceptions_maps_tls_certificate_error() -> None:
    @handle_exceptions
    def cert_fn():
        raise ssl.SSLCertVerificationError("certificate verify failed")

    with pytest.raises(click.ClickException, match="TLS certificate validation failed"):
        cert_fn()


@pytest.mark.anyio
async def test_async_handle_exceptions_maps_timeout_error() -> None:
    @async_handle_exceptions
    async def timeout_async_fn():
        raise TimeoutError("deadline exceeded while waiting for flasher")

    with pytest.raises(click.ClickException, match="Operation timed out"):
        await timeout_async_fn()


def test_handle_exceptions_with_reauth_still_maps_common_exceptions() -> None:
    calls = []

    def login_func(_config):
        calls.append(True)

    @handle_exceptions_with_reauthentication(login_func)
    def timeout_fn():
        raise TimeoutError("operation timed out")

    with pytest.raises(click.ClickException, match="Operation timed out"):
        timeout_fn()

    assert calls == []


def test_handle_exceptions_with_reauth_maps_keyboard_interrupt() -> None:
    calls = []

    def login_func(_config):
        calls.append(True)

    @handle_exceptions_with_reauthentication(login_func)
    def interrupt_fn():
        raise KeyboardInterrupt()

    with pytest.raises(click.ClickException, match="Cancelled by user"):
        interrupt_fn()

    assert calls == []


def test_handle_exceptions_maps_file_not_found() -> None:
    @handle_exceptions
    def missing_file_fn():
        raise FileNotFoundError("/tmp/missing.img")

    with pytest.raises(click.ClickException, match="File not found"):
        missing_file_fn()


def test_handle_exceptions_maps_connection_refused() -> None:
    @handle_exceptions
    def connection_refused_fn():
        raise ConnectionRefusedError("connection refused")

    with pytest.raises(click.ClickException, match="Connection was refused"):
        connection_refused_fn()


def test_handle_exceptions_maps_json_decode_error() -> None:
    @handle_exceptions
    def bad_json_fn():
        raise JSONDecodeError("Expecting value", "x", 0)

    with pytest.raises(click.ClickException, match="Received invalid JSON data"):
        bad_json_fn()


def test_handle_exceptions_maps_keyboard_interrupt() -> None:
    @handle_exceptions
    def interrupt_fn():
        raise KeyboardInterrupt()

    with pytest.raises(click.ClickException, match="Cancelled by user"):
        interrupt_fn()


def test_handle_exceptions_maps_click_abort() -> None:
    @handle_exceptions
    def abort_fn():
        raise click.Abort()

    with pytest.raises(click.ClickException, match="Aborted by user"):
        abort_fn()


def test_handle_exceptions_maps_grpc_unauthenticated() -> None:
    class MockGrpcError(Exception):
        def code(self):
            return type("Code", (), {"name": "UNAUTHENTICATED"})()

        def details(self):
            return "token expired"

    @handle_exceptions
    def grpc_auth_fn():
        raise MockGrpcError()

    with pytest.raises(click.ClickException, match="Authentication or authorization failed"):
        grpc_auth_fn()


def test_handle_exceptions_maps_grpc_invalid_argument() -> None:
    class MockGrpcError(Exception):
        def code(self):
            return type("Code", (), {"name": "INVALID_ARGUMENT"})()

        def details(self):
            return "invalid selector"

    @handle_exceptions
    def grpc_invalid_arg_fn():
        raise MockGrpcError()

    with pytest.raises(click.ClickException, match="Invalid request arguments"):
        grpc_invalid_arg_fn()


def test_handle_exceptions_maps_grpc_failed_precondition() -> None:
    class MockGrpcError(Exception):
        def code(self):
            return type("Code", (), {"name": "FAILED_PRECONDITION"})()

        def details(self):
            return "exporter is not ready"

    @handle_exceptions
    def grpc_precondition_fn():
        raise MockGrpcError()

    with pytest.raises(click.ClickException, match="precondition"):
        grpc_precondition_fn()


def test_handle_exceptions_maps_grpc_failed_precondition_without_details() -> None:
    class MockGrpcError(Exception):
        def code(self):
            return type("Code", (), {"name": "FAILED_PRECONDITION"})()

        def details(self):
            return ""

    @handle_exceptions
    def grpc_precondition_no_details_fn():
        raise MockGrpcError()

    with pytest.raises(click.ClickException, match="precondition"):
        grpc_precondition_no_details_fn()


# ---------------------------------------------------------------------------
# Tests for automatic retry after successful re-authentication (NS-REQ-1/3)
# ---------------------------------------------------------------------------


def _make_expired_connection_error():
    """Create a ConnectionError that looks like an expired-token error."""
    exc = JmpConnectionError("token expired")
    config = MagicMock(name="client_config")
    exc.set_config(config)
    return exc, config


def test_reauth_retries_on_success() -> None:
    """After successful re-auth the decorator retries and returns the result (TS-NS-1)."""
    call_count = 0

    def login_func(_config):
        pass  # success

    @handle_exceptions_with_reauthentication(login_func)
    def fn():
        nonlocal call_count
        call_count += 1  # ty: ignore[unresolved-reference]
        if call_count == 1:
            exc, _ = _make_expired_connection_error()
            raise exc
        return "sentinel"

    result = fn()
    assert result == "sentinel"
    assert call_count == 2


def test_reauth_failure_raises_click_exception() -> None:
    """If login_func raises, the decorator surfaces a ClickException (TS-NS-2)."""
    call_count = 0

    def login_func(_config):
        raise RuntimeError("IdP unreachable")

    @handle_exceptions_with_reauthentication(login_func)
    def fn():
        nonlocal call_count
        call_count += 1  # ty: ignore[unresolved-reference]
        exc, _ = _make_expired_connection_error()
        raise exc

    with pytest.raises(click.ClickException, match="Re-authentication failed"):
        fn()

    # The wrapped function should have been called only once (no retry).
    assert call_count == 1


def test_reauth_retry_bounded_to_one_attempt() -> None:
    """The decorator retries at most once; a second failure surfaces normally (TS-NS-3)."""
    call_count = 0

    def login_func(_config):
        pass  # always succeeds

    @handle_exceptions_with_reauthentication(login_func)
    def fn():
        nonlocal call_count
        call_count += 1  # ty: ignore[unresolved-reference]
        exc, _ = _make_expired_connection_error()
        raise exc

    with pytest.raises(click.ClickException):
        fn()

    # Original call + exactly one retry = 2 total.
    assert call_count == 2


class _MockGrpcError(Exception):
    def __init__(self, code_name: str, details: str):
        super().__init__(details)
        self._code_name = code_name
        self._details = details

    def code(self):
        return type("Code", (), {"name": self._code_name})()

    def details(self):
        return self._details


_WRAPPED_CONSOLE_IN_USE = (
    "Unexpected <class 'jumpstarter.streams.fanout.ExclusiveSessionActive'>: "
    "Console in use. Use --observe or release-console."
)


def test_handle_exceptions_maps_exclusive_session_active() -> None:
    from jumpstarter.streams.fanout import ExclusiveSessionActive

    @handle_exceptions
    def fn():
        raise ExclusiveSessionActive()

    with pytest.raises(click.ClickException, match="Console in use") as exc_info:
        fn()
    assert "Unexpected" not in str(exc_info.value)


def test_handle_exceptions_maps_wrapped_console_in_use_grpc_error() -> None:
    from anyio import BrokenResourceError

    @handle_exceptions
    def fn():
        raise BrokenResourceError from _MockGrpcError("UNKNOWN", _WRAPPED_CONSOLE_IN_USE)

    with pytest.raises(click.ClickException, match="Console in use") as exc_info:
        fn()
    assert "Unexpected" not in str(exc_info.value)


@pytest.mark.anyio
async def test_async_handle_exceptions_maps_console_in_use_in_exception_group() -> None:
    from anyio import BrokenResourceError

    @async_handle_exceptions
    async def fn():
        try:
            raise _MockGrpcError("UNKNOWN", _WRAPPED_CONSOLE_IN_USE)
        except _MockGrpcError as grpc_exc:
            wrapped = BrokenResourceError()
            wrapped.__cause__ = grpc_exc
            raise ExceptionGroup("unhandled errors in a TaskGroup", [wrapped]) from None

    with pytest.raises(click.ClickException, match="Console in use") as exc_info:
        await fn()
    assert "Unexpected" not in str(exc_info.value)
