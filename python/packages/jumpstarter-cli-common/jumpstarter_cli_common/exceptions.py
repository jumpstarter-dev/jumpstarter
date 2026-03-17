import asyncio
import json
import socket
import ssl
import types
from concurrent.futures import TimeoutError as FutureTimeoutError
from functools import wraps
from types import TracebackType
from typing import NoReturn

import click

from jumpstarter.common.exceptions import ConnectionError, JumpstarterException


class ClickExceptionRed(click.ClickException):
    def format_message(self) -> str:
        return click.style(self.message, fg="red")


def _append_details(base_message: str, details: str) -> str:
    return f"{base_message} Details: {details}" if details else base_message


def _map_runtime_exception(exc: BaseException, message: str, message_lower: str) -> click.ClickException | None:
    timeout_types = (TimeoutError, asyncio.TimeoutError, socket.timeout, FutureTimeoutError)
    if isinstance(exc, timeout_types):
        timeout_hint = (
            "Operation timed out. Check connectivity and retry. "
            "If this happened while flashing, verify the board is reachable/in flashing mode "
            "and increase retries or timeout."
        )
        return ClickExceptionRed(_append_details(timeout_hint, message))

    is_cert_verification_error = isinstance(exc, ssl.SSLCertVerificationError) or (
        isinstance(exc, ssl.SSLError) and "certificate verify failed" in message_lower
    )
    if is_cert_verification_error:
        cert_hint = (
            "TLS certificate validation failed. Verify the endpoint certificate chain or configure "
            "the correct CA certificate. Use insecure TLS only for testing."
        )
        return ClickExceptionRed(_append_details(cert_hint, message))

    if isinstance(exc, socket.gaierror):
        return ClickExceptionRed(
            "Could not resolve host name. Check the endpoint DNS name and network settings. "
            f"Details: {message}"
        )

    if isinstance(exc, ConnectionRefusedError):
        return ClickExceptionRed(
            "Connection was refused by the remote endpoint. Verify endpoint/port and that the service "
            f"is running. Details: {message}"
        )

    if isinstance(exc, FileNotFoundError):
        return ClickExceptionRed(f"File not found. Verify the path and retry. Details: {message}")

    if isinstance(exc, PermissionError):
        return ClickExceptionRed(f"Permission denied while accessing a required resource. Details: {message}")

    if isinstance(exc, json.JSONDecodeError):
        return ClickExceptionRed(
            "Received invalid JSON data from a remote endpoint. Verify the service/proxy response. "
            f"Details: {message}"
        )

    if isinstance(exc, click.Abort):
        return ClickExceptionRed("Aborted by user.")

    if isinstance(exc, OSError):
        return ClickExceptionRed(f"Local system error while performing the operation. Details: {message}")
    return None


def _extract_grpc_code_and_details(exc: BaseException) -> tuple[str | None, str]:
    code = None
    details = ""
    try:
        code_member = exc.code
        if callable(code_member):
            grpc_code = code_member()
            code = grpc_code.name if hasattr(grpc_code, "name") else str(grpc_code)
    except Exception:
        code = None

    try:
        details_member = exc.details
        if callable(details_member):
            details = str(details_member() or "")
    except Exception:
        details = ""
    return code, details


def _map_grpc_exception(exc: BaseException) -> click.ClickException | None:
    # gRPC status handling for common user-facing errors before they become opaque traces.
    code, details = _extract_grpc_code_and_details(exc)
    details_lower = details.lower()

    if code == "DEADLINE_EXCEEDED":
        return ClickExceptionRed(
            _append_details(
                "Operation timed out while waiting for a response from the service.",
                details,
            )
        )
    if code == "UNAVAILABLE" and (
        "certificate verify failed" in details_lower
        or "certificate" in details_lower
        or "tls" in details_lower
    ):
        return ClickExceptionRed(
            _append_details(
                "TLS connection failed while connecting to the service. Verify certificates/CA settings.",
                details,
            )
        )
    if code == "UNAVAILABLE":
        return ClickExceptionRed(
            _append_details(
                "Service is temporarily unavailable or unreachable. Verify endpoint/network and retry.",
                details,
            )
        )
    if code in ("UNAUTHENTICATED", "PERMISSION_DENIED"):
        return ClickExceptionRed(
            _append_details(
                "Authentication or authorization failed. Run 'jmp login' and verify access permissions.",
                details,
            )
        )
    if code == "INVALID_ARGUMENT":
        return ClickExceptionRed(
            _append_details(
                "Invalid request arguments were sent to the service. Verify command options and configuration.",
                details,
            )
        )
    return None


def _map_common_exception(exc: BaseException) -> click.ClickException | None:
    """Map common transport/runtime exceptions to user-friendly Click errors."""
    message = str(exc)
    message_lower = message.lower()
    if mapped := _map_runtime_exception(exc, message, message_lower):
        return mapped
    return _map_grpc_exception(exc)


def _map_cli_exception(exc: BaseException) -> click.ClickException | None:
    if common_exc := _map_common_exception(exc):
        return common_exc
    if isinstance(exc, JumpstarterException):
        return ClickExceptionRed(str(exc))
    if isinstance(exc, KeyboardInterrupt):
        return ClickExceptionRed("Cancelled by user.")
    if isinstance(exc, click.ClickException):
        return exc
    return None


def async_handle_exceptions(func):
    """Decorator to handle exceptions in async functions, including those wrapped in BaseExceptionGroup."""

    @wraps(func)
    async def wrapped(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except BaseExceptionGroup as eg:
            # Handle exceptions wrapped in ExceptionGroup (e.g., from task groups)
            for exc in leaf_exceptions(eg, fix_tracebacks=False):
                if cli_exc := _map_cli_exception(exc):
                    raise cli_exc from None
            # If no handled exceptions, re-raise the original group
            raise eg
        except Exception as e:
            if cli_exc := _map_cli_exception(e):
                raise cli_exc from None
            raise
        except KeyboardInterrupt as e:
            if cli_exc := _map_cli_exception(e):
                raise cli_exc from None
            raise

    return wrapped


def handle_exceptions(func):
    """Decorator to handle exceptions in blocking functions."""

    @wraps(func)
    def wrapped(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if cli_exc := _map_cli_exception(e):
                raise cli_exc from None
            raise
        except KeyboardInterrupt as e:
            if cli_exc := _map_cli_exception(e):
                raise cli_exc from None
            raise

    return wrapped


def _handle_connection_error_with_reauth(exc, login_func):
    """Handle ConnectionError with reauthentication logic."""
    if "expired" in str(exc).lower():
        click.echo(click.style("Token is expired, triggering re-authentication", fg="red"))
        config = exc.get_config()
        login_func(config)
        raise ClickExceptionRed("Please try again now") from None
    else:
        raise ClickExceptionRed(str(exc)) from None


def _handle_single_exception_with_reauth(exc, login_func):
    """Handle a single exception (may raise)."""
    if isinstance(exc, ConnectionError):
        _handle_connection_error_with_reauth(exc, login_func)
    elif cli_exc := _map_cli_exception(exc):
        raise cli_exc from None
    # Not handled: fall through


def _handle_exception_group_with_reauth(eg, login_func) -> NoReturn:
    """Handle exceptions wrapped in BaseExceptionGroup."""
    for exc in leaf_exceptions(eg, fix_tracebacks=False):
        _handle_single_exception_with_reauth(exc, login_func)
    # If no handled exceptions, re-raise the original group
    raise eg


def handle_exceptions_with_reauthentication(login_func):
    """Decorator to handle exceptions in blocking functions, including those wrapped in BaseExceptionGroup."""

    def decorator(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except BaseExceptionGroup as eg:
                _handle_exception_group_with_reauth(eg, login_func)
            except (ConnectionError, JumpstarterException, click.ClickException) as e:
                _handle_single_exception_with_reauth(e, login_func)
            except Exception as e:
                if cli_exc := _map_cli_exception(e):
                    raise cli_exc from None
                raise

        return wrapped

    return decorator


def find_exception_in_group(
    eg: BaseExceptionGroup, exc_type: type[BaseException], *, fix_tracebacks: bool = False
) -> BaseException | None:
    """
    Find the first exception of a specific type in an ExceptionGroup.

    Args:
        eg: The ExceptionGroup to search
        exc_type: The exception type to find
        fix_tracebacks: Whether to fix tracebacks in leaf exceptions

    Returns:
        The first matching exception, or None if not found
    """
    for exc in leaf_exceptions(eg, fix_tracebacks=fix_tracebacks):
        if isinstance(exc, exc_type):
            return exc
    return None


# https://peps.python.org/pep-0654/
def leaf_exceptions(self: BaseExceptionGroup, *, fix_tracebacks: bool = True) -> list[BaseException]:
    """
    Return a flat list of all 'leaf' exceptions.

    If fix_tracebacks is True, each leaf will have the traceback replaced
    with a composite so that frames attached to intermediate groups are
    still visible when debugging. Pass fix_tracebacks=False to disable
    this modification, e.g. if you expect to raise the group unchanged.
    """

    def _flatten(group: BaseExceptionGroup, parent_tb: TracebackType | None = None):
        group_tb = group.__traceback__
        combined_tb = _combine_tracebacks(parent_tb, group_tb)
        result = []
        for exc in group.exceptions:
            if isinstance(exc, BaseExceptionGroup):
                result.extend(_flatten(exc, combined_tb))
            elif fix_tracebacks:
                tb = _combine_tracebacks(combined_tb, exc.__traceback__)
                result.append(exc.with_traceback(tb))
            else:
                result.append(exc)
        return result

    return _flatten(self)


def _combine_tracebacks(
    tb1: TracebackType | None,
    tb2: TracebackType | None,
) -> TracebackType | None:
    """
    Combine two tracebacks, putting tb1 frames before tb2 frames.

    If either is None, return the other.
    """
    if tb1 is None:
        return tb2
    if tb2 is None:
        return tb1

    # Convert tb1 to a list of frames
    frames = []
    current = tb1
    while current is not None:
        frames.append((current.tb_frame, current.tb_lasti, current.tb_lineno))
        current = current.tb_next

    # Create a new traceback starting with tb2
    new_tb = tb2

    # Add frames from tb1 to the beginning (in reverse order)
    for frame, lasti, lineno in reversed(frames):
        new_tb = types.TracebackType(tb_next=new_tb, tb_frame=frame, tb_lasti=lasti, tb_lineno=lineno)

    return new_tb
