import inspect
from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass
from enum import Enum
from inspect import isasyncgenfunction, iscoroutinefunction, isfunction, isgeneratorfunction
from typing import Any, Final, get_origin

MARKER_MAGIC: Final[str] = "07c9b9cc"
MARKER_DRIVERCALL: Final[str] = "marker_drivercall"
MARKER_STREAMCALL: Final[str] = "marker_streamcall"
MARKER_STREAMING_DRIVERCALL: Final[str] = "marker_streamingdrivercall"
MARKER_TYPE_INFO: Final[str] = "marker_type_info"
MARKER_STREAM_METHOD: Final[str] = "marker_stream_method"


class CallType(Enum):
    """The RPC call type of an exported method."""

    UNARY = "unary"
    SERVER_STREAMING = "server_streaming"
    CLIENT_STREAMING = "client_streaming"
    BIDI_STREAMING = "bidi_streaming"


@dataclass(frozen=True)
class ExportedMethodInfo:
    """Metadata captured from an @export-decorated method's signature."""

    name: str
    call_type: CallType
    params: list[tuple[str, Any, Any]]  # (name, annotation, default)
    return_type: Any


def _is_generator_type(annotation: Any) -> bool:
    """Check if an annotation is AsyncGenerator or Generator."""
    origin = get_origin(annotation)
    return origin is AsyncGenerator or origin is Generator


def _infer_call_type(func) -> CallType:
    """Infer the RPC call type from a function's signature.

    - AsyncGenerator/Generator return type -> server streaming
    - AsyncGenerator parameter -> client streaming
    - Both -> bidi streaming
    - Otherwise -> unary
    """
    sig = inspect.signature(func)

    # Check if return type indicates streaming
    is_server_streaming = (
        isasyncgenfunction(func)
        or isgeneratorfunction(func)
        or _is_generator_type(sig.return_annotation)
    )

    # Check if any parameter is an AsyncGenerator (client streaming)
    is_client_streaming = False
    for param in sig.parameters.values():
        if param.name == "self":
            continue
        if _is_generator_type(param.annotation):
            is_client_streaming = True
            break

    if is_server_streaming and is_client_streaming:
        return CallType.BIDI_STREAMING
    elif is_server_streaming:
        return CallType.SERVER_STREAMING
    elif is_client_streaming:
        return CallType.CLIENT_STREAMING
    else:
        return CallType.UNARY


def export(func):
    """
    Decorator for exporting method as driver call
    """
    if isasyncgenfunction(func) or isgeneratorfunction(func):
        setattr(func, MARKER_STREAMING_DRIVERCALL, MARKER_MAGIC)
    elif iscoroutinefunction(func) or isfunction(func):
        setattr(func, MARKER_DRIVERCALL, MARKER_MAGIC)
    else:
        raise ValueError(f"unsupported exported function {func}")
    return func


def exportstream(func):
    """
    Decorator for exporting method as stream
    """
    setattr(func, MARKER_STREAMCALL, MARKER_MAGIC)
    return func


def driver(*, client: str | None = None):
    """Class decorator for proto-first driver classes — the Python analog of Rust's
    ``#[driver(client = "...")]`` and the JVM's ``@JumpstarterDriver(client = ...)``.

    A generated interface base already advertises its generated typed client, so the decorator is
    only needed to point a driver at a custom one::

        @driver(client="example.client.CyclingPowerClient")
        class ExamplePower(PowerInterface): ...
    """

    def wrap(cls):
        if client is not None:
            label = client

            def _client(cls) -> str:
                return label

            cls.client = classmethod(_client)
        return cls

    return wrap
