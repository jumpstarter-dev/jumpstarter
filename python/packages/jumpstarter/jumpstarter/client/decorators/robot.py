"""
Robot Framework decorators for driver clients.

Every public method of a client class becomes a Robot Framework keyword
automatically (see the ``jumpstarter-robot`` package). These decorators let a
driver author curate that, and behave like Robot Framework's own
``robot.api.deco`` without making the driver depend on Robot Framework::

    from jumpstarter.client.decorators import robot

    class PowerClient(DriverClient):
        @robot.keyword("Should Be On", tags=["assert"])
        def should_be_on(self) -> None:
            '''Fail unless the power status reports on.'''

        @robot.not_keyword
        def read(self): ...

They set the same attributes Robot Framework's decorators set (``robot_name``,
``robot_tags``, ``robot_types``, ``robot_not_keyword``, ``ROBOT_AUTO_KEYWORDS``),
so a driver that already depends on Robot Framework can use ``robot.api.deco``
instead and get the same keywords.
"""

import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import Any, overload

from ._frontend import update_frontend_meta

FRONTEND = "robot"


@overload
def keyword[F: Callable[..., Any]](name: F) -> F: ...


@overload
def keyword[F: Callable[..., Any]](
    name: str | None = None,
    tags: Sequence[str] = (),
    types: Mapping[str, Any] | Sequence[Any] | None = (),
    doc: str | None = None,
) -> Callable[[F], F]: ...


def keyword(name=None, tags=(), types=(), doc=None):
    """Mark a client method as a keyword, optionally naming and documenting it.

    ``name`` replaces the words derived from the method name; the driver's
    tree path is still prefixed, so ``@robot.keyword("Should Be On")`` on the
    ``power`` driver becomes ``Power Should Be On``. ``doc`` replaces the
    docstring. Works bare (``@robot.keyword``) or called.
    """
    if inspect.isroutine(name):
        return keyword()(name)

    def decorator(func):
        func.robot_name = name
        func.robot_tags = tuple(tags)
        func.robot_types = types
        if doc is not None:
            func.robot_doc = doc
        update_frontend_meta(func, FRONTEND, name=name, tags=tuple(tags), types=types, doc=doc, hidden=False)
        return func

    return decorator


def not_keyword[F: Callable[..., Any]](func: F) -> F:
    """Keep a public client method out of Robot Framework."""
    func.robot_not_keyword = True  # ty: ignore[unresolved-attribute]
    update_frontend_meta(func, FRONTEND, hidden=True)
    return func


@overload
def library[C: type](cls: C) -> C: ...


@overload
def library[C: type](*, auto_keywords: bool = False, doc: str | None = None) -> Callable[[C], C]: ...


def library(cls=None, *, auto_keywords=False, doc=None):
    """Configure how a client class is exposed, like ``robot.api.deco.library``.

    As in Robot Framework, decorating a class turns automatic keywords off by
    default, so only ``@robot.keyword`` methods are exposed; pass
    ``auto_keywords=True`` to keep every public method. ``doc`` replaces the
    class docstring as the library introduction.
    """
    if inspect.isclass(cls):
        return library()(cls)

    def decorator(klass):
        klass.ROBOT_AUTO_KEYWORDS = auto_keywords
        if doc is not None:
            klass.ROBOT_LIBRARY_DOC = doc
        update_frontend_meta(klass, FRONTEND, auto_keywords=auto_keywords, doc=doc)
        return klass

    return decorator


__all__ = ["keyword", "library", "not_keyword"]
