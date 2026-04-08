import inspect
from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass, field
from enum import Enum
from inspect import isasyncgenfunction, iscoroutinefunction, isfunction, isgeneratorfunction
from typing import Any, Final, get_args, get_origin


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

    - AsyncGenerator/Generator return type → server streaming
    - AsyncGenerator parameter → client streaming
    - Both → bidi streaming
    - Otherwise → unary
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
    """Decorator for exporting method as driver call.

    Validates that the method has complete type annotations
    for all parameters, then captures the signature as
    ExportedMethodInfo metadata. A missing return type
    annotation is treated as -> None.
    """
    sig = inspect.signature(func)

    # Validate all parameters (except self) have type annotations
    for param in sig.parameters.values():
        if param.name == "self":
            continue
        if param.annotation is inspect.Parameter.empty:
            raise TypeError(
                f"@export method {func.__qualname__}: parameter '{param.name}' "
                f"must have a type annotation."
            )

    # Missing return type is treated as -> None
    return_type = sig.return_annotation
    if return_type is inspect.Parameter.empty:
        return_type = None

    # Store type info for introspection
    type_info = ExportedMethodInfo(
        name=func.__name__,
        call_type=_infer_call_type(func),
        params=[
            (p.name, p.annotation, p.default)
            for p in sig.parameters.values()
            if p.name != "self"
        ],
        return_type=return_type,
    )
    setattr(func, MARKER_TYPE_INFO, type_info)

    # Existing marker logic (unchanged)
    if isasyncgenfunction(func) or isgeneratorfunction(func):
        setattr(func, MARKER_STREAMING_DRIVERCALL, MARKER_MAGIC)
    elif iscoroutinefunction(func) or isfunction(func):
        setattr(func, MARKER_DRIVERCALL, MARKER_MAGIC)
    else:
        raise ValueError(f"unsupported exported function {func}")
    return func


def streammethod(func):
    """Decorator for marking an abstract interface method as a raw byte stream.

    Use on abstract methods in DriverInterface subclasses to indicate that
    the method is a stream constructor (bidirectional byte channel via
    RouterService), not a typed RPC via DriverCall.

    Usage::

        @interface_name("network")
        class NetworkInterface(DriverInterface):
            @abstractmethod
            @streammethod
            async def connect(self) -> None: ...

    Concrete implementations use @exportstream instead. The builder uses
    this marker to emit a bidi streaming RPC with BytesValue types.
    """
    setattr(func, MARKER_STREAM_METHOD, MARKER_MAGIC)
    return func


def driverinterface(name: str, *, version: str = "v1"):
    """Class decorator that declares a Jumpstarter driver interface.

    The name should be PascalCase (matching the class name convention).
    It is stored as-is and converted to snake_case for the proto package
    path: ``jumpstarter.interfaces.{snake_case_name}.{version}``

    Usage::

        @driverinterface("Power")
        class PowerInterface(DriverInterface):
            ...

        @driverinterface("StorageMux", version="v2")
        class StorageMuxInterfaceV2(DriverInterface):
            ...

    If not applied, the builder derives the name from the class name by
    stripping an "Interface" suffix, with version "v1".
    """

    def decorator(cls):
        cls.__interface_name__ = name
        cls.__interface_version__ = version
        return cls

    return decorator


def exportstream(func):
    """Decorator for exporting method as stream.

    Captures signature metadata and marks the method as a stream call.
    A missing return type annotation is treated as -> None.
    """
    sig = inspect.signature(func)

    # Missing return type is treated as -> None
    return_type = sig.return_annotation
    if return_type is inspect.Parameter.empty:
        return_type = None

    # Store type info for introspection
    type_info = ExportedMethodInfo(
        name=func.__name__,
        call_type=CallType.BIDI_STREAMING,  # exportstream is always bidi
        params=[
            (p.name, p.annotation, p.default)
            for p in sig.parameters.values()
            if p.name != "self"
        ],
        return_type=return_type,
    )
    setattr(func, MARKER_TYPE_INFO, type_info)

    setattr(func, MARKER_STREAMCALL, MARKER_MAGIC)
    return func
