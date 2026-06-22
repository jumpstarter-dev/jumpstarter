"""Click error handling for the `j` driver-client CLI.

Inlined from the retired `jumpstarter-cli-common` package (its `exceptions.py`),
trimmed to what `j` needs: map common runtime/transport errors to red Click
errors, and flatten `BaseExceptionGroup`s raised by the anyio task groups. The
reauth/blocking-CLI and gRPC-status helpers were dropped — the controller path is
Rust now, so Python no longer sees gRPC status exceptions.
"""

import json
import socket
import ssl
import types
from functools import wraps
from types import TracebackType

import click

from jumpstarter.common.exceptions import JumpstarterException


class ClickExceptionRed(click.ClickException):
    def format_message(self) -> str:
        return click.style(self.message, fg="red")


def _append_details(base_message: str, details: str) -> str:
    return f"{base_message} Details: {details}" if details else base_message


def _map_runtime_exception(exc: BaseException, message: str, message_lower: str) -> click.ClickException | None:
    if isinstance(exc, TimeoutError):
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


def _map_cli_exception(exc: BaseException) -> click.ClickException | None:
    message = str(exc)
    if mapped := _map_runtime_exception(exc, message, message.lower()):
        return mapped
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
            for exc in leaf_exceptions(eg, fix_tracebacks=False):
                if cli_exc := _map_cli_exception(exc):
                    raise cli_exc from None
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


def find_exception_in_group(
    eg: BaseExceptionGroup, exc_type: type[BaseException], *, fix_tracebacks: bool = False
) -> BaseException | None:
    """Find the first exception of a specific type in an ExceptionGroup."""
    for exc in leaf_exceptions(eg, fix_tracebacks=fix_tracebacks):
        if isinstance(exc, exc_type):
            return exc
    return None


# https://peps.python.org/pep-0654/
def leaf_exceptions(self: BaseExceptionGroup, *, fix_tracebacks: bool = True) -> list[BaseException]:
    """Return a flat list of all 'leaf' exceptions.

    If fix_tracebacks is True, each leaf gets a composite traceback so frames
    attached to intermediate groups stay visible; pass False to raise the group
    unchanged afterwards.
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
    """Combine two tracebacks, putting tb1 frames before tb2 frames."""
    if tb1 is None:
        return tb2
    if tb2 is None:
        return tb1

    frames = []
    current = tb1
    while current is not None:
        frames.append((current.tb_frame, current.tb_lasti, current.tb_lineno))
        current = current.tb_next

    new_tb = tb2
    for frame, lasti, lineno in reversed(frames):
        new_tb = types.TracebackType(tb_next=new_tb, tb_frame=frame, tb_lasti=lasti, tb_lineno=lineno)

    return new_tb
