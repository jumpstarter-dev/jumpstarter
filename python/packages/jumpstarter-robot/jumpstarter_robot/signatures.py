"""Turn client method signatures into Robot Framework argument specs.

Robot Framework converts keyword arguments from the types a library reports.
Client methods are annotated for Python callers, so the annotations are
filtered down to the types Robot Framework can convert. Anything else (an
opendal ``Operator``, a pydantic model) is reported untyped, so the value a
test passes reaches the method unchanged; pydantic models and dataclasses also
get a coercer that accepts a JSON string or a dictionary.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import enum
import inspect
import logging
import os
import pathlib
import types
import typing
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, TypeAdapter

logger = logging.getLogger(__name__)

_SCALARS = (
    str,
    int,
    float,
    bool,
    bytes,
    bytearray,
    decimal.Decimal,
    datetime.datetime,
    datetime.date,
    datetime.timedelta,
    pathlib.Path,
    pathlib.PurePath,
    type(None),
)
_CONTAINERS = (list, tuple, set, frozenset, dict)


@dataclasses.dataclass(frozen=True)
class ArgumentSpec:
    """What Robot Framework needs to know about a keyword's arguments."""

    args: list[str | tuple[str, Any]]
    types: dict[str, Any]
    coercers: dict[str, TypeAdapter]
    signature: inspect.Signature

    def bind(self, args: list[Any], kwargs: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
        """Apply the coercers to the arguments of one call."""
        if not self.coercers:
            return list(args), dict(kwargs)
        bound = self.signature.bind_partial(*args, **kwargs)
        for name, adapter in self.coercers.items():
            if name in bound.arguments:
                bound.arguments[name] = _coerce(name, adapter, bound.arguments[name])
        return list(bound.args), dict(bound.kwargs)


def _coerce(name: str, adapter: TypeAdapter, value: Any) -> Any:
    from robot.api import Failure

    try:
        if isinstance(value, (str, bytes)):
            try:
                return adapter.validate_json(value)
            except ValueError:
                return adapter.validate_python(value)
        return adapter.validate_python(value)
    except ValueError as e:
        raise Failure(f"Invalid value for argument '{name}': {e}") from None


def resolve_hints(func: Callable[..., Any]) -> dict[str, Any]:
    """Type hints of ``func``, tolerating names only imported under TYPE_CHECKING."""
    try:
        return typing.get_type_hints(func)
    except Exception:
        logger.debug("cannot resolve all type hints of %s; resolving them one by one", func, exc_info=True)
    hints: dict[str, Any] = {}
    namespace = dict(getattr(func, "__globals__", {}))
    for name, annotation in getattr(func, "__annotations__", {}).items():
        if isinstance(annotation, str):
            try:
                annotation = eval(annotation, namespace)
            except Exception:  # noqa: BLE001 - unresolvable, leave untyped
                logger.debug("leaving %s of %s untyped: cannot resolve %r", name, func, annotation)
                continue
        hints[name] = annotation
    return hints


def _is_model(tp: Any) -> bool:
    return inspect.isclass(tp) and (issubclass(tp, BaseModel) or dataclasses.is_dataclass(tp))


def _robot_class(tp: type) -> type | None:
    if tp in _SCALARS or tp in _CONTAINERS or issubclass(tp, enum.Enum) or typing.is_typeddict(tp):
        return tp
    if issubclass(tp, pathlib.PurePath):
        return pathlib.Path
    return None


def _is_pathlike(tp: Any) -> bool:
    return (typing.get_origin(tp) or tp) is os.PathLike


def _robot_union(tp: Any) -> Any | None:
    """The union of the members Robot Framework can convert, or None if any other member is allowed.

    Robot rejects, or converts to the wrong type, a value of a member it was
    not told about, so a union with one (``int | Model``, ``str | Operator``)
    is left untyped and its values reach the method unchanged.
    """
    members = typing.get_args(tp)
    kept = []
    for member in members:
        converted = robot_type(member)
        if converted is not None:
            kept.append(converted)
        elif not (_is_pathlike(member) and str in members):
            return None
        # else str | os.PathLike (PathBuf) collapses to str: Robot passes paths as text
    if not kept or kept == [type(None)]:
        return None
    if len(kept) == 1:
        return kept[0]
    return typing.Union[tuple(kept)]  # noqa: UP007 - built at runtime


def robot_type(tp: Any) -> Any | None:
    """The part of ``tp`` Robot Framework can convert, or None to leave it untyped."""
    if tp is Any or tp is inspect.Parameter.empty:
        return None
    if inspect.isclass(tp):
        return _robot_class(tp)
    origin = typing.get_origin(tp)
    if origin is typing.Literal:
        return tp
    if origin in (typing.Union, types.UnionType):
        return _robot_union(tp)
    if origin in _CONTAINERS:
        converted = [robot_type(arg) for arg in typing.get_args(tp) if arg is not Ellipsis]
        return origin if any(c is None for c in converted) else tp
    return None


def _needs_coercion(tp: Any) -> bool:
    if _is_model(tp):
        return True
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        return any(_needs_coercion(arg) for arg in typing.get_args(tp))
    return False


def _robot_args(params: list[inspect.Parameter]) -> list[str | tuple[str, Any]]:
    """Robot Framework's argument spec: names, (name, default), ``/``, ``*``, ``*args``, ``**kwargs``."""
    args: list[str | tuple[str, Any]] = []
    positional_only = False
    star_written = False
    for param in params:
        if param.kind is param.POSITIONAL_ONLY:
            positional_only = True
        elif positional_only:
            args.append("/")
            positional_only = False
        if param.kind is param.VAR_POSITIONAL:
            args.append(f"*{param.name}")
            star_written = True
        elif param.kind is param.VAR_KEYWORD:
            args.append(f"**{param.name}")
        else:
            if param.kind is param.KEYWORD_ONLY and not star_written:
                args.append("*")
                star_written = True
            args.append(param.name if param.default is param.empty else (param.name, param.default))
    if positional_only:
        args.append("/")
    return args


def _conversions(
    params: list[inspect.Parameter], hints: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, TypeAdapter]]:
    rf_types: dict[str, Any] = {}
    coercers: dict[str, TypeAdapter] = {}
    for param in params:
        hint = hints.get(param.name, param.empty)
        if hint is param.empty:
            continue
        converted = robot_type(hint)
        if converted is not None:
            rf_types[param.name] = converted
        elif _needs_coercion(hint):
            try:
                coercers[param.name] = TypeAdapter(hint)
            except Exception:
                logger.debug("no coercer for %s: %r", param.name, hint, exc_info=True)
    return rf_types, coercers


def argument_spec(func: Callable[..., Any], *, skip: int = 1) -> ArgumentSpec:
    """Build the Robot Framework argument spec of ``func``.

    The first ``skip`` positional parameters are dropped: ``self`` for methods
    taken from a class, or values a keyword supplies itself.
    """
    signature = inspect.signature(func)
    params = list(signature.parameters.values())
    while skip and params and params[0].kind in (params[0].POSITIONAL_ONLY, params[0].POSITIONAL_OR_KEYWORD):
        params = params[1:]
        skip -= 1
    signature = signature.replace(parameters=params)
    rf_types, coercers = _conversions(params, resolve_hints(func))
    return ArgumentSpec(args=_robot_args(params), types=rf_types, coercers=coercers, signature=signature)


def source_of(func: Callable[..., Any]) -> str | None:
    try:
        path = inspect.getsourcefile(func)
        _, line = inspect.getsourcelines(func)
    except (OSError, TypeError):
        return None
    return f"{path}:{line}" if path else None


def convert_call(func: Callable[..., Any], args: list[Any] | tuple, kwargs: dict[str, Any]) -> tuple[list, dict]:
    """Convert a generic keyword's text arguments to ``func``'s annotated types.

    Generated keywords get this from Robot Framework itself. Keywords that call
    a method by name (`Call Driver`, `Open Driver Context`) only see strings,
    so they convert here with the same rules: ``wait=5`` reaches
    ``cycle(wait: int)`` as ``5``. Arguments that do not bind, or have no
    convertible annotation, pass through unchanged and the method reports them.
    """
    from robot.api import TypeInfo

    try:
        spec = argument_spec(func, skip=0)
        bound = spec.signature.bind_partial(*args, **kwargs)
    except (TypeError, ValueError):
        return list(args), dict(kwargs)
    for name, value in list(bound.arguments.items()):
        hint = spec.types.get(name)
        if hint is None:
            continue
        kind = spec.signature.parameters[name].kind
        info = TypeInfo.from_type_hint(hint)
        if kind is inspect.Parameter.VAR_POSITIONAL:
            bound.arguments[name] = tuple(info.convert(v, name) if isinstance(v, str) else v for v in value)
        elif kind is inspect.Parameter.VAR_KEYWORD:
            bound.arguments[name] = {k: info.convert(v, k) if isinstance(v, str) else v for k, v in value.items()}
        elif isinstance(value, str):
            bound.arguments[name] = info.convert(value, name)
    return spec.bind(list(bound.args), dict(bound.kwargs))
