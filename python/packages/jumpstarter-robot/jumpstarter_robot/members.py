"""The public driver API of a client class, read statically.

Keywords are generated from these members, without an instance and so without
a lease.
"""

from __future__ import annotations

import functools
import inspect
from dataclasses import dataclass
from typing import Any, Literal

from jumpstarter.client.base import DriverClient, StubDriverClient
from jumpstarter.client.introspect import _BASE_METHODS, _INTERNAL_METHODS

MemberKind = Literal["method", "generator", "contextmanager", "property", "async"]


@dataclass(frozen=True)
class ClientMember:
    """One public member of a client class, as a frontend sees it.

    ``function`` is the plain function behind the member (the getter for a
    property), so frontends can read its signature, type hints and decorator
    metadata without touching an instance.
    """

    name: str
    kind: MemberKind
    function: Any
    owner: type
    # a staticmethod: ``function`` then has no ``self`` to leave out of its signature
    static: bool = False


def _is_client_base(klass: type) -> bool:
    """Whether ``klass`` is client plumbing rather than driver API.

    Decided by what the class is, not where it lives, so a client from an
    out-of-tree package is driver API like any other. The jumpstarter client
    base classes are plumbing, and so is every class that is not a
    DriverClient at all: ``can.BusABC`` behind CanClient, anyio's context
    manager mixins and ``object`` carry methods that are not the driver's
    interface.
    """
    # NativeDriverClient: the native-core client base, where a build provides one
    return (
        klass in (DriverClient, StubDriverClient)
        or not issubclass(klass, DriverClient)
        or klass.__name__ == "NativeDriverClient"
    )


def _classify(raw: Any) -> tuple[MemberKind, Any] | None:
    if isinstance(raw, property):
        return ("property", raw.fget) if raw.fget is not None else None
    if isinstance(raw, functools.cached_property):
        return "property", raw.func
    if isinstance(raw, (staticmethod, classmethod)):
        raw = raw.__func__
    if not inspect.isfunction(raw):
        return None
    if inspect.iscoroutinefunction(raw) or inspect.isasyncgenfunction(raw):
        return "async", raw
    if inspect.isgeneratorfunction(raw):
        return "generator", raw
    wrapped = getattr(raw, "__wrapped__", None)
    if wrapped is not None and (inspect.isgeneratorfunction(wrapped) or inspect.isasyncgenfunction(wrapped)):
        # Only contextlib turns a generator function into a context manager. Any
        # other functools.wraps decorator (pydantic's validate_call on
        # OpendalClient.list) still returns the generator itself.
        if getattr(raw, "__globals__", {}).get("__name__") == "contextlib":
            return "contextmanager", raw
        return ("generator" if inspect.isgeneratorfunction(wrapped) else "async"), raw
    return "method", raw


def iter_client_members(cls: type) -> list[ClientMember]:
    """List the public driver API of a client class, without an instance.

    Walks the class and its bases statically (``vars()``), so properties that
    would make a driver call are never evaluated. The first definition along
    the MRO wins, and client plumbing classes are skipped whole, which keeps a
    subclass override such as PowerClient's ``status`` method while dropping
    the base ``status`` property. ``cli``, ``close`` and
    the other base helpers in ``_BASE_METHODS`` are never driver API.
    """
    members: list[ClientMember] = []
    seen: set[str] = set()
    for klass in cls.__mro__:
        if _is_client_base(klass):
            continue
        for name, raw in vars(klass).items():
            if name in seen:
                continue
            seen.add(name)
            if name.startswith("_") or name in _BASE_METHODS or name in _INTERNAL_METHODS or name.endswith("_async"):
                continue
            classified = _classify(raw)
            if classified is None:
                continue
            kind, function = classified
            members.append(
                ClientMember(
                    name=name, kind=kind, function=function, owner=klass, static=isinstance(raw, staticmethod)
                )
            )
    return members
