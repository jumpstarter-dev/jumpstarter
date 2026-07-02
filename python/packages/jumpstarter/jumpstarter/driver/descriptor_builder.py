"""
Build google.protobuf.FileDescriptorProto from driver interface classes.

Introspects abstract / @export-decorated methods on interface classes, maps
Python type annotations to protobuf descriptors using the type_mapping module,
and assembles a complete FileDescriptorProto.

A driver "interface" is any class that declares the per-interface contract:
either an abstract interface class (methods marked with @abstractmethod and/or
@export) or a concrete Driver subclass (methods marked with @export). The
builder does not require a particular base class or metaclass; it discovers
methods by introspection.

This descriptor feeds the Rust core's on-demand dynamic dispatch.
"""

from __future__ import annotations

import dataclasses
import inspect
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Generator, Iterator
from typing import Any, get_args, get_origin

from google.protobuf.descriptor_pb2 import (
    DescriptorProto,
    FieldDescriptorProto,
    FileDescriptorProto,
    FileDescriptorSet,
    MethodDescriptorProto,
    ServiceDescriptorProto,
    SourceCodeInfo,
)

from pydantic import BaseModel
from .decorators import (
    MARKER_STREAM_METHOD,
    MARKER_STREAMCALL,
    MARKER_TYPE_INFO,
    CallType,
    ExportedMethodInfo,
)
from .type_mapping import (
    EMPTY_TYPE,
    VALUE_TYPE,
    TypeMappingResult,
    add_synthetic_oneof,
    map_python_type,
)

_log = logging.getLogger(__name__)

# FileDescriptorProto field numbers for source_code_info paths
_FDP_MESSAGE_TYPE = 4  # FileDescriptorProto.message_type
_FDP_SERVICE = 6  # FileDescriptorProto.service
_SDP_METHOD = 2  # ServiceDescriptorProto.method
_DP_FIELD = 2  # DescriptorProto.field


def resolve_interface_class(driver) -> type | None:
    """Find the driver's INTERFACE class — the base that declares the per-interface contract.

    Proto-first drivers subclass a codegen-generated base that itself directly subclasses
    ``ProtoInterface`` (e.g. ``NativeMockPower(PowerInterface)``) — the interface is that
    generated base. Legacy drivers subclass a hand-written interface ABC alongside ``Driver``
    (e.g. ``MockPower(PowerInterface, Driver)``) — the interface is the MRO entry that defines
    ``client`` in its own namespace and is NOT itself a ``Driver`` subclass (which excludes
    ``Driver`` and the concrete driver). Returns ``None`` if no such base exists (the driver
    then has no native surface).

    Callers typically do ``resolve_interface_class(driver) or type(driver)`` — the concrete class is
    the fallback so a driver without a separate interface ABC still exposes its ``@export`` surface.
    """
    from .base import Driver
    from .proto_interface import ProtoInterface

    for cls in type(driver).__mro__:
        if cls is not ProtoInterface and ProtoInterface in cls.__bases__:
            return cls
    for cls in type(driver).__mro__:
        if cls is Driver or cls is object:
            continue
        if "client" in cls.__dict__ and not issubclass(cls, Driver):
            return cls
    return None


def build_file_descriptor(  # noqa: C901 — linear assembly of one FileDescriptorProto
    interface_class: type, version: str | None = None
) -> FileDescriptorProto:
    """Build a FileDescriptorProto from a Python interface class.

    Introspects abstract / @export methods, maps Python type annotations to
    protobuf field/message/service descriptors, and returns a self-contained
    FileDescriptorProto that fully describes the interface.

    Args:
        interface_class: A driver interface class (abstract interface or
            concrete Driver subclass) to introspect.
        version: Version string override for the proto package (e.g., "v1").
            If None, uses __interface_version__ from the class, defaulting to "v1".

    Returns:
        A FileDescriptorProto describing the interface as a proto service.
    """
    interface_name = interface_class.__name__

    # Use __interface_name__ if declared (PascalCase), otherwise strip "Interface" suffix
    pascal_name = getattr(interface_class, "__interface_name__", None)
    if not pascal_name:
        pascal_name = interface_name
        if pascal_name.endswith("Interface"):
            pascal_name = pascal_name[: -len("Interface")]

    # Convert to snake_case for package path and file name
    snake_name = _to_snake_case(pascal_name)

    # Use explicit version, then __interface_version__, then default "v1"
    if version is None:
        version = getattr(interface_class, "__interface_version__", None) or "v1"

    package = f"jumpstarter.interfaces.{snake_name}.{version}"

    fd = FileDescriptorProto(
        name=f"{snake_name}.proto",
        package=package,
        syntax="proto3",
    )

    # Track which well-known type dependencies we need
    needs_empty = False
    needs_value = False

    # Track source_code_info locations for doc comments
    locations: list[SourceCodeInfo.Location] = []

    service = ServiceDescriptorProto(name=interface_name)
    service_index = 0  # We only add one service
    message_index = 0  # Running index for messages added to fd

    # Service-level docstring (interface class docstring)
    class_doc = inspect.cleandoc(interface_class.__doc__) if interface_class.__doc__ else None
    if class_doc:
        _add_comment(locations, [_FDP_SERVICE, service_index], class_doc)

    # Check if we need a StreamData message for @exportstream bidi methods
    needs_stream_data = False

    interface_methods = _get_interface_methods(interface_class)
    for method_index, (method_name, method_info) in enumerate(interface_methods):
        # Detect @exportstream bidi methods with no typed params/return
        # These are pure byte streams that use StreamData instead of Empty
        is_exportstream_bytes = (
            method_info.call_type == CallType.BIDI_STREAMING
            and not method_info.params
            and (
                method_info.return_type is None
                or method_info.return_type is type(None)
                or method_info.return_type is inspect.Parameter.empty
                or method_info.return_type == "None"
            )
        )

        if is_exportstream_bytes:
            needs_stream_data = True
            # Use StreamData for byte-carrying bidi streams
            input_type = f".{package}.StreamData"
            output_type = f".{package}.StreamData"
        else:
            # Build request message
            request_msg, req_deps = _build_request_message(
                method_name, method_info.params, package
            )
            # Build response message
            response_msg, resp_deps = _build_response_message(
                method_name, method_info.return_type, method_info.call_type, package
            )

            # Track dependencies
            if req_deps.get("empty") or resp_deps.get("empty"):
                needs_empty = True
            if req_deps.get("value") or resp_deps.get("value"):
                needs_value = True

            # Add messages to the file descriptor and track indices
            if request_msg is not None:
                fd.message_type.append(request_msg)
                message_index += 1
            if response_msg is not None:
                fd.message_type.append(response_msg)
                # Add message-level docstring if this is a model type
                resp_doc = _get_type_docstring(method_info.return_type, method_info.call_type)
                if resp_doc:
                    _add_comment(
                        locations,
                        [_FDP_MESSAGE_TYPE, message_index],
                        resp_doc,
                    )
                # Add field-level docstrings for the response message
                _add_field_comments(
                    locations,
                    [_FDP_MESSAGE_TYPE, message_index],
                    method_info.return_type,
                    method_info.call_type,
                )
                message_index += 1

            # Determine input/output type names
            if request_msg is not None:
                input_type = f".{package}.{request_msg.name}"
            else:
                input_type = EMPTY_TYPE

            if response_msg is not None:
                output_type = f".{package}.{response_msg.name}"
            else:
                output_type = EMPTY_TYPE

        # Create method descriptor
        service.method.append(
            MethodDescriptorProto(
                name=_to_pascal_case(method_name),
                input_type=input_type,
                output_type=output_type,
                server_streaming=method_info.call_type
                in (CallType.SERVER_STREAMING, CallType.BIDI_STREAMING),
                client_streaming=method_info.call_type
                in (CallType.CLIENT_STREAMING, CallType.BIDI_STREAMING),
            )
        )

        # Method-level docstring
        method_doc = _get_method_docstring(interface_class, method_name)
        if method_doc:
            _add_comment(
                locations,
                [_FDP_SERVICE, service_index, _SDP_METHOD, method_index],
                method_doc,
            )

    # Add the StreamData message if any @exportstream methods need it
    if needs_stream_data:
        stream_data_msg = DescriptorProto(name="StreamData")
        stream_data_msg.field.append(
            FieldDescriptorProto(
                name="payload",
                number=1,
                type=FieldDescriptorProto.TYPE_BYTES,
                label=FieldDescriptorProto.LABEL_OPTIONAL,
            )
        )
        # Insert at the beginning so it's available to all methods
        fd.message_type.insert(0, stream_data_msg)
        _add_comment(
            locations,
            [_FDP_MESSAGE_TYPE, 0],
            "Byte payload for bidirectional stream methods (@exportstream).\n",
        )
        # Shift message indices for comments that were already added
        # (they reference indices that just moved by +1)
        for loc in locations:
            if (
                len(loc.path) >= 2
                and loc.path[0] == _FDP_MESSAGE_TYPE
                and loc != locations[-1]  # Don't shift the one we just added
            ):
                loc.path[1] += 1

    fd.service.append(service)

    # Add dependencies
    if needs_empty:
        fd.dependency.append("google/protobuf/empty.proto")
    if needs_value:
        fd.dependency.append("google/protobuf/struct.proto")

    # Set source_code_info if we have any comments
    if locations:
        sci = SourceCodeInfo()
        for loc in locations:
            sci.location.append(loc)
        fd.source_code_info.CopyFrom(sci)

    return fd


def build_file_descriptor_set(
    interface_class: type, version: str | None = None
) -> FileDescriptorSet:
    """Build a self-contained ``FileDescriptorSet`` for an interface: its own
    ``FileDescriptorProto`` plus its transitive well-known-type dependency files
    (``google/protobuf/empty.proto`` etc.), ordered **deps-first**, so a descriptor pool
    can be built with no external imports to resolve.

    This is the single source of the interface's native wire contract — both the exporter
    host (which serializes it into each ``DriverNode.descriptor_set``) and the proto
    marshaller (which decodes/encodes the per-driver proto against it) consume the SAME set,
    so the advertised wire and the decoded wire can never diverge.
    """
    fdp = build_file_descriptor(interface_class, version)
    file_set = FileDescriptorSet()
    # Dependencies first (the deps-first ordering a descriptor pool requires), then the
    # interface file itself last.
    file_set.file.extend(_dependency_fdps(fdp, set()))
    file_set.file.append(fdp)
    return file_set


def _dependency_fdps(fdp: FileDescriptorProto, _seen: set[str]) -> list[FileDescriptorProto]:
    """Collect the transitive well-known-type dependency ``FileDescriptorProto``s referenced by
    ``fdp.dependency`` (e.g. ``google/protobuf/empty.proto`` → its compiled descriptor, and recurse
    into ITS deps), ordered deps-first and deduped. Each dependency's FDP comes from the compiled
    protobuf module's ``DESCRIPTOR`` (``CopyToProto``). Unknown/unresolvable dependency names are
    skipped (logged) — only the well-known types the builder emits are mapped here."""
    # The builder only ever emits empty.proto / struct.proto (Value lives in struct.proto).
    out: list[FileDescriptorProto] = []
    for dep_name in fdp.dependency:
        if dep_name in _seen:
            continue
        _seen.add(dep_name)
        try:
            if dep_name == "google/protobuf/empty.proto":
                from google.protobuf import empty_pb2 as _mod
            elif dep_name in ("google/protobuf/struct.proto", "google/protobuf/value.proto"):
                from google.protobuf import struct_pb2 as _mod
            else:
                _log.warning("native descriptor: unknown well-known dependency %s; skipping", dep_name)
                continue
        except ImportError as exc:  # pragma: no cover — protobuf wkt always ships these
            _log.warning("native descriptor: cannot import dependency %s: %s", dep_name, exc)
            continue
        dep_fdp = FileDescriptorProto()
        _mod.DESCRIPTOR.CopyToProto(dep_fdp)
        # Recurse first so this dep's own dependencies precede it (deps-first ordering).
        out.extend(_dependency_fdps(dep_fdp, _seen))
        out.append(dep_fdp)
    return out


def _add_comment(
    locations: list[SourceCodeInfo.Location],
    path: list[int],
    comment: str,
) -> None:
    """Add a leading comment at the given source_code_info path."""
    loc = SourceCodeInfo.Location()
    loc.path.extend(path)
    # Ensure comment ends with newline for proper proto formatting
    if not comment.endswith("\n"):
        comment += "\n"
    loc.leading_comments = comment
    locations.append(loc)


def _get_method_docstring(interface_class: type, method_name: str) -> str | None:
    """Get the docstring for a method on the interface class."""
    # Walk MRO to find the method with the docstring
    for cls in interface_class.__mro__:
        if method_name in cls.__dict__:
            method = cls.__dict__[method_name]
            doc = inspect.getdoc(method)
            if doc:
                return doc
            break
    return None


def _get_type_docstring(return_type: Any, call_type: CallType) -> str | None:
    """Get the docstring for a return type (e.g., BaseModel class)."""
    actual_type = return_type
    if call_type in (CallType.SERVER_STREAMING, CallType.BIDI_STREAMING):
        if _is_generator_type(return_type):
            actual_type = _unwrap_generator_type(return_type)

    # Only extract docstrings from user-defined types, not builtins
    _BUILTIN_TYPES = (str, int, float, bool, bytes, type(None), dict, list, set, tuple)
    if isinstance(actual_type, type) and actual_type not in _BUILTIN_TYPES:
        doc = actual_type.__doc__
        if doc:
            return inspect.cleandoc(doc)
    return None


def _add_field_comments(
    locations: list[SourceCodeInfo.Location],
    message_path: list[int],
    return_type: Any,
    call_type: CallType,
) -> None:
    """Add field-level docstrings from BaseModel or dataclass fields."""
    actual_type = return_type
    if call_type in (CallType.SERVER_STREAMING, CallType.BIDI_STREAMING):
        if _is_generator_type(return_type):
            actual_type = _unwrap_generator_type(return_type)

    if not isinstance(actual_type, type):
        return

    # Pydantic BaseModel field descriptions
    if issubclass(actual_type, BaseModel):
        for field_index, (_field_name, field_info) in enumerate(
            actual_type.model_fields.items()
        ):
            desc = field_info.description
            if desc:
                _add_comment(
                    locations,
                    message_path + [_DP_FIELD, field_index],
                    desc,
                )
    # Dataclass fields with metadata containing 'description'
    elif dataclasses.is_dataclass(actual_type):
        import dataclasses as dc

        for field_index, field in enumerate(dc.fields(actual_type)):
            desc = field.metadata.get("description") if field.metadata else None
            if desc:
                _add_comment(
                    locations,
                    message_path + [_DP_FIELD, field_index],
                    desc,
                )


def _to_pascal_case(name: str) -> str:
    """Convert snake_case to PascalCase."""
    return "".join(word.capitalize() for word in name.split("_"))


def _to_snake_case(name: str) -> str:
    """Convert PascalCase to snake_case."""
    import re
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()


def _is_generator_type(annotation: Any) -> bool:
    """Check if an annotation is a streaming-yield type: AsyncGenerator/Generator, or their
    idiomatic hint forms AsyncIterator/Iterator (what generated proto-first interfaces use)."""
    origin = get_origin(annotation)
    return origin in (AsyncGenerator, Generator, AsyncIterator, Iterator)


def _unwrap_generator_type(annotation: Any) -> Any:
    """Extract the yield type from AsyncGenerator[T, ...] / Generator[T, ...] /
    AsyncIterator[T] / Iterator[T] (the yield type is the first arg in all four)."""
    args = get_args(annotation)
    if args:
        return args[0]
    return Any


def _get_interface_methods(
    interface_class: type,
) -> list[tuple[str, ExportedMethodInfo]]:
    """Get all methods from an interface class that should appear in the proto.

    For abstract interfaces (no Driver in MRO): walks the full MRO to collect
    abstract methods from the interface hierarchy.

    For concrete driver classes (a driver WITHOUT a separate interface ABC):
    walks the full MRO collecting every @export/@exportstream method, including
    INHERITED ones from parent driver classes — so such a driver still exposes
    its complete native surface (not only methods defined in its own namespace).
    Methods resolve to their most-derived definition (a subclass override wins).

    A codegen-generated proto-first base (a direct ProtoInterface subclass) is a Driver
    subclass too, but its contract is its ABSTRACT methods — it takes the abstract branch.
    """
    from .base import Driver
    from .proto_interface import ProtoInterface

    methods: dict[str, ExportedMethodInfo] = {}

    is_concrete_driver = issubclass(interface_class, Driver) and ProtoInterface not in interface_class.__bases__

    if is_concrete_driver:
        # Walk the MRO base→derived so a derived override wins, collecting @export methods at each
        # level. This covers a concrete driver's inherited @export methods (the no-interface case),
        # not just the ones defined directly on the class.
        for cls in reversed(interface_class.__mro__):
            if cls is Driver or cls is object:
                continue
            _collect_own_methods(cls, methods)
    else:
        # Abstract interface: walk MRO up to (but not including) Driver/object
        _collect_interface_methods(interface_class, methods)

    return sorted(methods.items())


def _collect_own_methods(
    cls: type, methods: dict[str, ExportedMethodInfo],
) -> None:
    """Collect @export/@exportstream methods defined directly on a class."""
    from .decorators import MARKER_DRIVERCALL, MARKER_STREAMING_DRIVERCALL

    for name, attr in cls.__dict__.items():
        if name.startswith("_") or name == "client":
            continue
        if not callable(attr):
            continue

        # Resolve the actual method (may be wrapped by @export etc.)
        method = getattr(cls, name, None)
        if method is None:
            continue

        # Only include methods with @export or @exportstream markers
        has_export = (
            getattr(method, MARKER_DRIVERCALL, None)
            or getattr(method, MARKER_STREAMING_DRIVERCALL, None)
            or getattr(method, MARKER_STREAMCALL, None)
        )
        if not has_export:
            continue

        type_info = getattr(method, MARKER_TYPE_INFO, None)
        if type_info is not None:
            methods[name] = type_info
        else:
            info = _introspect_method(name, method)
            if info is not None:
                methods[name] = info


def _collect_interface_methods(
    interface_class: type, methods: dict[str, ExportedMethodInfo],
) -> None:
    """Collect abstract and @streammethod methods from an interface hierarchy."""
    for name in dir(interface_class):
        if name.startswith("_") or name == "client":
            continue

        method = getattr(interface_class, name, None)
        if method is None:
            continue

        # Check if it has ExportedMethodInfo from @export
        type_info = getattr(method, MARKER_TYPE_INFO, None)
        if type_info is not None:
            methods[name] = type_info
            continue

        # Check if it's an abstract method or @streammethod
        is_abstract = getattr(method, "__isabstractmethod__", False)
        has_stream_marker = getattr(method, MARKER_STREAM_METHOD, None)
        if not is_abstract and not has_stream_marker:
            continue

        info = _introspect_method(name, method)
        if info is not None:
            methods[name] = info


def _introspect_method(name: str, method: Any) -> ExportedMethodInfo | None:
    """Build ExportedMethodInfo from a method's signature via introspection."""
    try:
        sig = inspect.signature(method)
    except (ValueError, TypeError):
        return None

    # Determine call type
    call_type = _infer_call_type_from_sig(method, sig)

    # Check for @exportstream or @streammethod marker
    if getattr(method, MARKER_STREAMCALL, None) or getattr(method, MARKER_STREAM_METHOD, None):
        call_type = CallType.BIDI_STREAMING
    # An interface ABC declares its @exportstream method as an abstract @asynccontextmanager (the
    # concrete driver carries the MARKER_STREAMCALL; the interface does not). Detect that shape — a
    # contextmanager wrapping an async generator that yields a byte stream — as a bidi byte channel,
    # so the descriptor models it as a StreamData stream (not a unary Empty→Empty) even when built
    # from the interface.
    elif _is_async_context_manager_method(method):
        call_type = CallType.BIDI_STREAMING

    params = [
        (p.name, p.annotation, p.default)
        for p in sig.parameters.values()
        if p.name != "self"
    ]

    return ExportedMethodInfo(
        name=name,
        call_type=call_type,
        params=params,
        return_type=sig.return_annotation,
    )


def _is_async_context_manager_method(method: Any) -> bool:
    """Whether ``method`` is an ``@asynccontextmanager``-wrapped method — the shape of an abstract
    ``@exportstream`` byte-stream method on an interface ABC.

    ``@asynccontextmanager``'s helper is a plain (sync) function whose ``__wrapped__`` (set via
    ``functools.wraps``) is the original ``async def`` — a coroutine function when the body is an
    abstract ``...`` (the interface), or an async generator when the body ``yield``s (the concrete
    driver). So the tell is: the wrapped target is async while the wrapper method itself is not. This
    distinguishes a byte stream (``@asynccontextmanager async def connect(self): ...``) from a regular
    ``@export`` (the method is itself a coroutine / async generator)."""
    from inspect import isasyncgenfunction, iscoroutinefunction

    wrapped = getattr(method, "__wrapped__", None)
    if wrapped is None:
        return False
    wrapped_is_async = iscoroutinefunction(wrapped) or isasyncgenfunction(wrapped)
    method_is_async = iscoroutinefunction(method) or isasyncgenfunction(method)
    return wrapped_is_async and not method_is_async


def _infer_call_type_from_sig(method: Any, sig: inspect.Signature) -> CallType:
    """Infer call type from method signature and function type."""
    from inspect import isasyncgenfunction, isgeneratorfunction

    # Check return type for streaming
    is_server_streaming = (
        isasyncgenfunction(method)
        or isgeneratorfunction(method)
        or _is_generator_type(sig.return_annotation)
    )

    # Check params for client streaming
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


def _build_request_message(
    method_name: str,
    params: list[tuple[str, Any, Any]],
    package: str,
) -> tuple[DescriptorProto | None, dict[str, bool]]:
    """Build a request message descriptor from method parameters.

    Returns (message, dependencies) where dependencies tracks which
    well-known types are needed.
    """
    deps: dict[str, bool] = {}

    if not params:
        deps["empty"] = True
        return None, deps

    msg_name = f"{_to_pascal_case(method_name)}Request"
    msg = DescriptorProto(name=msg_name)

    for i, (param_name, annotation, _default) in enumerate(params, start=1):
        if annotation is inspect.Parameter.empty:
            # Fallback for unannotated params
            result = TypeMappingResult(
                field_type=FieldDescriptorProto.TYPE_MESSAGE,
                type_name=VALUE_TYPE,
            )
            deps["value"] = True
        else:
            result = map_python_type(annotation, package, param_name)

        field = FieldDescriptorProto(
            name=param_name,
            number=i,
            type=result.field_type,
            label=result.label,
        )
        if result.type_name:
            field.type_name = result.type_name
            if EMPTY_TYPE in result.type_name:
                deps["empty"] = True
            if VALUE_TYPE in result.type_name:
                deps["value"] = True
        if result.is_optional:
            field.proto3_optional = True

        add_synthetic_oneof(msg, field)
        msg.field.append(field)

        # Add any nested messages/enums
        for nested in result.messages:
            msg.nested_type.append(nested)
        for nested_enum in result.enums:
            msg.enum_type.append(nested_enum)

    return msg, deps


def _build_response_message(
    method_name: str,
    return_type: Any,
    call_type: CallType,
    package: str,
) -> tuple[DescriptorProto | None, dict[str, bool]]:
    """Build a response message descriptor from the return type.

    Returns (message, dependencies).
    """
    deps: dict[str, bool] = {}

    # Unwrap generator types for streaming methods
    actual_type = return_type
    if call_type in (CallType.SERVER_STREAMING, CallType.BIDI_STREAMING):
        if _is_generator_type(return_type):
            actual_type = _unwrap_generator_type(return_type)

    # None return type -> Empty (handle both actual None and string 'None' from PEP 563)
    if (
        actual_type is None
        or actual_type is type(None)
        or actual_type is inspect.Parameter.empty
        or actual_type == "None"
    ):
        deps["empty"] = True
        return None, deps

    result = map_python_type(actual_type, package, f"{method_name}_response")

    # If it maps to Empty, no message needed
    if result.type_name == EMPTY_TYPE:
        deps["empty"] = True
        return None, deps

    # For simple types, wrap in a response message
    msg_name = f"{_to_pascal_case(method_name)}Response"
    msg = DescriptorProto(name=msg_name)

    # If the result is already a message type with its own descriptor,
    # we can use it directly or wrap it
    if result.field_type == FieldDescriptorProto.TYPE_MESSAGE and result.messages:
        # The type already has its own message — return it directly
        return result.messages[0], deps

    # Wrap primitive/simple types in a response message with a 'value' field
    field = FieldDescriptorProto(
        name="value",
        number=1,
        type=result.field_type,
        label=result.label,
    )
    if result.type_name:
        field.type_name = result.type_name
        if VALUE_TYPE in result.type_name:
            deps["value"] = True

    msg.field.append(field)

    # Add nested types
    for nested in result.messages:
        msg.nested_type.append(nested)
    for nested_enum in result.enums:
        msg.enum_type.append(nested_enum)

    return msg, deps
