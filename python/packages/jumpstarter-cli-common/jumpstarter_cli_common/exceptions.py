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


def _map_common_exception(exc: BaseException) -> click.ClickException | None:
    """Map common transport/runtime exceptions to user-friendly Click errors."""
    message = str(exc)
    message_lower = message.lower()

    timeout_types = (TimeoutError, asyncio.TimeoutError, socket.timeout, FutureTimeoutError)
    if isinstance(exc, timeout_types):
        timeout_hint = (
            "Operation timed out. Check connectivity and retry. "
            "If this happened while flashing, verify the board is reachable/in flashing mode "
            "and increase retries or timeout."
        )
        if message:
            timeout_hint = f"{timeout_hint} Details: {message}"
        return ClickExceptionRed(timeout_hint)

    is_cert_verification_error = isinstance(exc, ssl.SSLCertVerificationError) or (
        isinstance(exc, ssl.SSLError) and "certificate verify failed" in message_lower
    )
    if is_cert_verification_error:
        cert_hint = (
            "TLS certificate validation failed. Verify the endpoint certificate chain or configure "
            "the correct CA certificate. Use insecure TLS only for testing."
        )
        if message:
            cert_hint = f"{cert_hint} Details: {message}"
        return ClickExceptionRed(cert_hint)

    if isinstance(exc, socket.gaierror):
        return ClickExceptionRed(
            f"Could not resolve host name. Check the endpoint DNS name and network settings. Details: {message}"
        )

    if isinstance(exc, ConnectionRefusedError):
        return ClickExceptionRed(
            f"Connection was refused by the remote endpoint. Verify endpoint/port and that the service is running. Details: {message}"
        )

    if isinstance(exc, FileNotFoundError):
        return ClickExceptionRed(f"File not found. Verify the path and retry. Details: {message}")

    if isinstance(exc, PermissionError):
        return ClickExceptionRed(f"Permission denied while accessing a required resource. Details: {message}")

    if isinstance(exc, json.JSONDecodeError):
        return ClickExceptionRed(
            f"Received invalid JSON data from a remote endpoint. Verify the service/proxy response. Details: {message}"
        )

    if isinstance(exc, click.Abort):
        return ClickExceptionRed("Aborted by user.")

    if isinstance(exc, OSError):
        return ClickExceptionRed(f"Local system error while performing the operation. Details: {message}")

    # gRPC status handling for common user-facing errors before they become opaque traces.
    code = None
    details = ""
    if hasattr(exc, "code") and callable(getattr(exc, "code")):
        try:
            grpc_code = exc.code()
            code = getattr(grpc_code, "name", str(grpc_code))
        except Exception:
            code = None
    if hasattr(exc, "details") and callable(getattr(exc, "details")):
        try:
            details = str(exc.details() or "")
        except Exception:
            details = ""
    details_lower = details.lower()

    if code == "DEADLINE_EXCEEDED":
        detail_suffix = f" Details: {details}" if details else ""
        return ClickExceptionRed(
            "Operation timed out while waiting for a response from the service."
            f"{detail_suffix}"
        )
    if code == "UNAVAILABLE" and (
        "certificate verify failed" in details_lower
        or "certificate" in details_lower
        or "tls" in details_lower
    ):
        detail_suffix = f" Details: {details}" if details else ""
        return ClickExceptionRed(
            "TLS connection failed while connecting to the service. Verify certificates/CA settings."
            f"{detail_suffix}"
        )
    if code == "UNAVAILABLE":
        detail_suffix = f" Details: {details}" if details else ""
        return ClickExceptionRed(
            "Service is temporarily unavailable or unreachable. Verify endpoint/network and retry."
            f"{detail_suffix}"
        )
    if code in ("UNAUTHENTICATED", "PERMISSION_DENIED"):
        detail_suffix = f" Details: {details}" if details else ""
        return ClickExceptionRed(
            "Authentication or authorization failed. Run 'jmp login' and verify access permissions."
            f"{detail_suffix}"
        )
    if code == "INVALID_ARGUMENT":
        detail_suffix = f" Details: {details}" if details else ""
        return ClickExceptionRed(
            "Invalid request arguments were sent to the service. Verify command options and configuration."
            f"{detail_suffix}"
        )

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
                if common_exc := _map_common_exception(exc):
                    raise common_exc from None
                elif isinstance(exc, JumpstarterException):
                    raise ClickExceptionRed(str(exc)) from None
                elif isinstance(exc, click.ClickException):
                    raise exc from None
            # If no handled exceptions, re-raise the original group
            raise eg
        except JumpstarterException as e:
            raise ClickExceptionRed(str(e)) from None
        except KeyboardInterrupt:
            raise ClickExceptionRed("Cancelled by user.") from None
        except click.ClickException:
            raise  # if it was already a click exception from the cli commands, just re-raise it
        except Exception as e:
            if common_exc := _map_common_exception(e):
                raise common_exc from None
            raise

    return wrapped


def handle_exceptions(func):
    """Decorator to handle exceptions in blocking functions."""

    @wraps(func)
    def wrapped(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except JumpstarterException as e:
            raise ClickExceptionRed(str(e)) from None
        except KeyboardInterrupt:
            raise ClickExceptionRed("Cancelled by user.") from None
        except click.ClickException:
            raise  # if it was already a click exception from the cli commands, just re-raise it
        except Exception as e:
            if common_exc := _map_common_exception(e):
                raise common_exc from None
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
    elif common_exc := _map_common_exception(exc):
        raise common_exc from None
    elif isinstance(exc, JumpstarterException):
        raise ClickExceptionRed(str(exc)) from None
    elif isinstance(exc, click.ClickException):
        raise exc from None
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
            except Exception:
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
