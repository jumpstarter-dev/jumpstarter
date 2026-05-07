import ssl
from json import JSONDecodeError

import click
import pytest

from jumpstarter_cli_common.exceptions import (
    async_handle_exceptions,
    handle_exceptions,
    handle_exceptions_with_reauthentication,
)


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


def test_handle_exceptions_with_reauth_retries_on_expired_token() -> None:
    """After successful re-auth, the command is retried automatically."""
    from jumpstarter.common.exceptions import ConnectionError

    call_count = [0]

    def login_func(config):
        config.token = "new_token"

    @handle_exceptions_with_reauthentication(login_func)
    def command_fn(config=None):
        call_count[0] += 1
        if call_count[0] == 1:
            exc = ConnectionError("token is expired")
            exc.set_config(config)
            raise exc
        return "result"

    config = type("Config", (), {"token": "old_token"})()
    result = command_fn(config=config)

    assert result == "result"
    assert call_count[0] == 2


def test_handle_exceptions_with_reauth_does_not_retry_twice() -> None:
    """If retry also fails with expired token, the error propagates."""
    from jumpstarter.common.exceptions import ConnectionError

    login_calls = [0]

    def login_func(config):
        login_calls[0] += 1

    @handle_exceptions_with_reauthentication(login_func)
    def always_expired_fn(config=None):
        exc = ConnectionError("token is expired")
        exc.set_config(config)
        raise exc

    config = type("Config", (), {"token": "tok"})()
    with pytest.raises(click.ClickException):
        always_expired_fn(config=config)

    assert login_calls[0] == 1


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
