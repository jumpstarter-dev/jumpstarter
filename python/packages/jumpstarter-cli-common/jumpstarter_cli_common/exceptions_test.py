import ssl
from json import JSONDecodeError

import click
import pytest

from jumpstarter_cli_common.exceptions import (
    async_handle_exceptions,
    handle_exceptions,
    handle_exceptions_with_reauthentication,
)


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
