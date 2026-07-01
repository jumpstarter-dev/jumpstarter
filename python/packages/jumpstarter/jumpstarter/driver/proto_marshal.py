"""Proto ⇄ native marshalling for the native (proto-bytes) driver dispatch path.

This is the **inverse** of ``descriptor_builder`` + ``type_mapping``: where those turn a driver's
``@export`` interface into a ``FileDescriptorSet`` (native types → proto), this turns real proto
request bytes into positional Python call args and a native return value back into proto response
bytes. It is the Python analog of the Rust core's ``jumpstarter_codec::dynamic`` — except the value
boundary is a real proto message (via ``google.protobuf``), not JSON, so the exporter host can serve
``DriverHost.forward_unary``/``forward_server_stream`` with proto bytes (parity with the JVM
``GrpcServiceDriverHost``) instead of the legacy JSON ``driver_call`` codec.

Lockstep guarantee: the marshaller builds its ``DescriptorPool`` from the SAME
``build_file_descriptor_set(iface)`` the host advertises in ``describe()``, and drives the
native-side conversion off the method's ``ExportedMethodInfo`` (the same introspection
``descriptor_builder`` used to emit the fields). So the wire the host advertises and the wire the
marshaller decodes/encodes can never diverge.

Byte channels (``@exportstream``) and resources are NOT handled here — they ride the separate
``open_stream``/``stream_*`` seam. Only unary and server-streaming ``@export`` methods get a spec.
"""

from __future__ import annotations

import dataclasses
import enum
import logging
from typing import Any, Literal, Union, get_args, get_origin
from uuid import UUID

from google.protobuf import message_factory
from google.protobuf.descriptor import Descriptor, FieldDescriptor
from google.protobuf.descriptor_pool import DescriptorPool
from google.protobuf.message import Message
from pydantic import BaseModel

from .decorators import ExportedMethodInfo
from .descriptor_builder import (
    _get_interface_methods,
    _is_generator_type,
    _to_pascal_case,
    _unwrap_generator_type,
    build_file_descriptor_set,
    resolve_interface_class,
)

_log = logging.getLogger(__name__)

# Well-known message full names the value converters special-case.
_VALUE_FULL_NAME = "google.protobuf.Value"
_STRUCT_FULL_NAME = "google.protobuf.Struct"


def _is_repeated(f: FieldDescriptor) -> bool:
    """Whether a field is ``repeated`` — ``is_repeated`` on modern protobuf (the upb backend drops
    ``.label``), falling back to the ``label`` constant on older runtimes."""
    r = getattr(f, "is_repeated", None)
    if r is not None:
        return r
    return f.label == FieldDescriptor.LABEL_REPEATED


@dataclasses.dataclass
class MethodSpec:
    """Everything the host needs to dispatch one native RPC, computed once per driver."""

    grpc_path: str  # "/pkg.Service/MethodName"
    export_name: str  # the snake_case @export method name on the driver
    info: ExportedMethodInfo  # native param annotations + return type (the native truth)
    input_desc: Descriptor
    output_desc: Descriptor
    input_cls: type[Message]
    output_cls: type[Message]
    server_streaming: bool
    # Precomputed encode shape for the return value (see descriptor_builder._build_response_message):
    #   "empty"   → no fields, None/Empty
    #   "wrapper" → synthetic <Method>Response with a single `value` field (scalar/list/enum return)
    #   "bare"    → the return type's own message (BaseModel/dataclass), fields by name
    output_kind: str
    # The native return annotation, generator-unwrapped for streaming (Optional preserved).
    return_annotation: Any


@dataclasses.dataclass
class DriverMarshaller:
    """A driver's native dispatch table: its descriptor pool + per-path method specs."""

    pool: DescriptorPool
    service_full_name: str
    methods: dict[str, MethodSpec]  # keyed by grpc_path


def build_marshaller(driver) -> DriverMarshaller | None:
    """Build the native dispatch table for ``driver``, or ``None`` if it has no usable native
    surface (uninspectable interface) — the host maps ``None`` to ``Unimplemented`` so the Rust core
    falls back to the legacy JSON ``driver_call`` path. Never raises for an ordinary driver."""
    iface = resolve_interface_class(driver) or type(driver)
    try:
        file_set = build_file_descriptor_set(iface)
        pool = DescriptorPool()
        for fdp in file_set.file:
            pool.Add(fdp)
        # The interface file is last (deps-first); it carries the one service.
        interface_fdp = file_set.file[-1]
        if not interface_fdp.service:
            return None
        service_full = f"{interface_fdp.package}.{interface_fdp.service[0].name}"
        svc = pool.FindServiceByName(service_full)

        # Map the descriptor's PascalCase method name back to the driver's snake @export method +
        # its introspected info, round-trip-safe (match _to_pascal_case(export_name), never a lossy
        # de-camel of the proto name).
        by_pascal: dict[str, tuple[str, ExportedMethodInfo]] = {
            _to_pascal_case(name): (name, info)
            for name, info in _get_interface_methods(iface)
        }

        methods: dict[str, MethodSpec] = {}
        for m in svc.methods:
            # Byte channels (@exportstream) are client-streaming/bidi and ride open_stream, not this
            # seam. Only unary and pure server-streaming methods get a native spec here.
            if m.client_streaming:
                continue
            matched = by_pascal.get(m.name)
            if matched is None:
                continue
            export_name, info = matched
            input_desc = m.input_type
            output_desc = m.output_type
            spec = MethodSpec(
                grpc_path=f"/{service_full}/{m.name}",
                export_name=export_name,
                info=info,
                input_desc=input_desc,
                output_desc=output_desc,
                input_cls=message_factory.GetMessageClass(input_desc),
                output_cls=message_factory.GetMessageClass(output_desc),
                server_streaming=m.server_streaming,
                output_kind=_output_kind(output_desc, info, m.server_streaming),
                return_annotation=_return_annotation(info, m.server_streaming),
            )
            methods[spec.grpc_path] = spec
        return DriverMarshaller(pool=pool, service_full_name=service_full, methods=methods)
    except Exception as exc:  # noqa: BLE001 — an uninspectable driver must not crash; fall back
        _log.warning(
            "native marshaller build failed for %s (%s); no native surface",
            type(driver).__name__,
            exc,
        )
        return None


def decode_request(spec: MethodSpec, body: bytes) -> list[Any]:
    """Decode a request message's bytes into positional Python call args (field order = param
    order; an Empty request → ``[]``)."""
    msg = spec.input_cls()
    msg.ParseFromString(body)
    args: list[Any] = []
    # One request field per @export param, in order (descriptor_builder numbers fields by param
    # index) — strict=True asserts that 1:1 invariant.
    for fdesc, (_pname, annotation, _default) in zip(spec.input_desc.fields, spec.info.params, strict=True):
        args.append(_decode_field(msg, fdesc, annotation))
    return args


def encode_response(spec: MethodSpec, value: Any) -> bytes:
    """Encode a native return value (or one streamed item) into response message bytes."""
    if spec.output_kind == "empty":
        return spec.output_cls().SerializeToString()  # b"" for Empty
    out = spec.output_cls()
    if spec.output_kind == "wrapper":
        _encode_field(out, spec.output_desc.fields[0], spec.return_annotation, value)
    elif value is not None:  # bare message from a BaseModel/dataclass return
        _encode_message_fields(out, spec.output_desc, _strip_optional(spec.return_annotation), value)
    return out.SerializeToString()


# --- shape helpers ----------------------------------------------------------------------


def _return_annotation(info: ExportedMethodInfo, server_streaming: bool) -> Any:
    ret = info.return_type
    if server_streaming and _is_generator_type(ret):
        return _unwrap_generator_type(ret)
    return ret


def _output_kind(output_desc: Descriptor, info: ExportedMethodInfo, server_streaming: bool) -> str:
    if len(output_desc.fields) == 0:
        return "empty"
    ret = _strip_optional(_return_annotation(info, server_streaming))
    ret_is_model = (isinstance(ret, type) and issubclass(ret, BaseModel)) or dataclasses.is_dataclass(ret)
    is_wrapper_shape = len(output_desc.fields) == 1 and output_desc.fields[0].name == "value"
    # A wrapper is the synthetic <Method>Response{value}; a BaseModel/dataclass return is always a
    # bare message even in the rare case its sole field is literally named `value`.
    return "wrapper" if (is_wrapper_shape and not ret_is_model) else "bare"


def _strip_optional(annotation: Any) -> Any:
    """Unwrap ``Optional[T]`` / ``Union[T, None]`` to ``T`` (leave other annotations untouched)."""
    if get_origin(annotation) is Union:
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _native_field_annotations(native_type: Any) -> dict[str, Any]:
    """Field-name → annotation for a native message type (BaseModel/dataclass), else ``{}``."""
    if isinstance(native_type, type) and issubclass(native_type, BaseModel):
        return {n: fi.annotation for n, fi in native_type.model_fields.items()}
    if dataclasses.is_dataclass(native_type):
        return {f.name: f.type for f in dataclasses.fields(native_type)}
    return {}


# --- decode (wire → native) -------------------------------------------------------------


def _decode_field(msg: Message, fdesc: FieldDescriptor, annotation: Any) -> Any:
    name = fdesc.name
    if _is_repeated(fdesc):
        inner = get_args(annotation)[0] if get_args(annotation) else Any
        items = [_decode_single(v, fdesc, inner) for v in getattr(msg, name)]
        return set(items) if get_origin(annotation) is set else items
    if fdesc.has_presence and not msg.HasField(name):
        return None  # proto3 optional / message left unset → None
    return _decode_single(getattr(msg, name), fdesc, _strip_optional(annotation))


def _decode_single(val: Any, fdesc: FieldDescriptor, annotation: Any) -> Any:
    if fdesc.type == FieldDescriptor.TYPE_MESSAGE:
        full = fdesc.message_type.full_name
        if full == _VALUE_FULL_NAME:
            return _pb_value_to_py(val)
        if full == _STRUCT_FULL_NAME:
            return {k: _pb_value_to_py(v) for k, v in val.fields.items()}
        return _decode_message(val, fdesc.message_type, _strip_optional(annotation))
    if fdesc.type == FieldDescriptor.TYPE_ENUM:
        return _decode_enum(val, annotation)
    if annotation is UUID:
        return UUID(val)
    return val


def _decode_message(sub: Message, msg_desc: Descriptor, native_type: Any) -> Any:
    anns = _native_field_annotations(native_type)
    kwargs = {f.name: _decode_field(sub, f, anns.get(f.name, Any)) for f in msg_desc.fields}
    if isinstance(native_type, type) and (issubclass(native_type, BaseModel) or dataclasses.is_dataclass(native_type)):
        return native_type(**kwargs)
    return kwargs  # unknown native type → plain dict


def _decode_enum(number: int, annotation: Any) -> Any:
    ann = _strip_optional(annotation)
    if get_origin(ann) is Literal:
        vals = get_args(ann)
        return vals[number - 1] if 1 <= number <= len(vals) else None
    if isinstance(ann, type) and issubclass(ann, enum.Enum):
        members = list(ann)
        return members[number - 1] if 1 <= number <= len(members) else None
    return number


def _pb_value_to_py(v: Message) -> Any:
    kind = v.WhichOneof("kind")
    if kind == "null_value" or kind is None:
        return None
    if kind == "number_value":
        return v.number_value
    if kind == "string_value":
        return v.string_value
    if kind == "bool_value":
        return v.bool_value
    if kind == "struct_value":
        return {k: _pb_value_to_py(val) for k, val in v.struct_value.fields.items()}
    if kind == "list_value":
        return [_pb_value_to_py(x) for x in v.list_value.values]
    return None


# --- encode (native → wire) -------------------------------------------------------------


def _encode_message_fields(out: Message, msg_desc: Descriptor, native_type: Any, value: Any) -> None:
    anns = _native_field_annotations(native_type)
    for f in msg_desc.fields:
        _encode_field(out, f, anns.get(f.name, Any), _native_getattr(value, f.name))


def _native_getattr(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _encode_field(out: Message, fdesc: FieldDescriptor, annotation: Any, native_val: Any) -> None:
    name = fdesc.name
    if _is_repeated(fdesc):
        if native_val is None:
            return
        inner = get_args(annotation)[0] if get_args(annotation) else Any
        container = getattr(out, name)
        for item in native_val:
            if fdesc.type == FieldDescriptor.TYPE_MESSAGE:
                _encode_message_value(container.add(), fdesc, _strip_optional(inner), item)
            elif fdesc.type == FieldDescriptor.TYPE_ENUM:
                container.append(_encode_enum(item, inner))
            else:
                container.append(_encode_scalar(item))
        return
    if native_val is None:
        return  # leave unset (proto3 optional stays absent; a singular scalar stays at default)
    if fdesc.type == FieldDescriptor.TYPE_MESSAGE:
        _encode_message_value(getattr(out, name), fdesc, _strip_optional(annotation), native_val)
    elif fdesc.type == FieldDescriptor.TYPE_ENUM:
        setattr(out, name, _encode_enum(native_val, annotation))
    else:
        setattr(out, name, _encode_scalar(native_val))


def _encode_message_value(sub: Message, fdesc: FieldDescriptor, annotation: Any, native_val: Any) -> None:
    full = fdesc.message_type.full_name
    if full == _VALUE_FULL_NAME:
        _py_to_pb_value(native_val, sub)
    elif full == _STRUCT_FULL_NAME:
        for k, val in (native_val or {}).items():
            _py_to_pb_value(val, sub.fields[k])
    else:
        _encode_message_fields(sub, fdesc.message_type, annotation, native_val)


def _encode_scalar(native_val: Any) -> Any:
    if isinstance(native_val, UUID):
        return str(native_val)
    return native_val


def _encode_enum(native_val: Any, annotation: Any) -> int:
    ann = _strip_optional(annotation)
    if get_origin(ann) is Literal:
        vals = list(get_args(ann))
        try:
            return vals.index(native_val) + 1
        except ValueError:
            return 0
    if isinstance(ann, type) and issubclass(ann, enum.Enum):
        members = list(ann)
        try:
            return members.index(native_val) + 1
        except ValueError:
            return 0
    return native_val if isinstance(native_val, int) else 0


def _py_to_pb_value(v: Any, out: Message) -> None:
    if v is None:
        out.null_value = 0
    elif isinstance(v, bool):
        out.bool_value = v
    elif isinstance(v, (int, float)):
        out.number_value = float(v)
    elif isinstance(v, str):
        out.string_value = v
    elif isinstance(v, dict):
        for k, val in v.items():
            _py_to_pb_value(val, out.struct_value.fields[k])
    elif isinstance(v, (list, tuple, set)):
        for x in v:
            _py_to_pb_value(x, out.list_value.values.add())
    else:  # last-resort: stringify unknown leaf (matches JSON-Value best-effort)
        out.string_value = str(v)


__all__ = [
    "DriverMarshaller",
    "MethodSpec",
    "build_marshaller",
    "decode_request",
    "encode_response",
]
