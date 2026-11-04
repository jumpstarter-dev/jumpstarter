"""Read keyword curation from client classes.

Driver authors curate keywords with ``jumpstarter.client.decorators.robot``
or Robot Framework's own ``robot.api.deco``; both set the same attributes,
which is all this module looks at.
"""

from __future__ import annotations

import fnmatch
import inspect
from collections.abc import Iterable
from typing import Any

from .naming import title
from jumpstarter.client.introspect import ClientMember


def auto_keywords(cls: type) -> bool:
    """Whether undecorated public methods become keywords (``@robot.library`` turns it off)."""
    return bool(getattr(cls, "ROBOT_AUTO_KEYWORDS", True))


def is_exposed(member: ClientMember, cls: type) -> bool:
    function = member.function
    if getattr(function, "robot_not_keyword", False):
        return False
    if hasattr(function, "robot_name"):
        return True
    return auto_keywords(cls)


def method_words(member: ClientMember) -> str:
    """The keyword words for a member: the ``@keyword`` name, else its name in words."""
    name = getattr(member.function, "robot_name", None)
    return name if name else title(member.name)


def tags(member: ClientMember) -> tuple[str, ...]:
    return tuple(getattr(member.function, "robot_tags", ()) or ())


def documentation(member: ClientMember) -> str:
    doc = getattr(member.function, "robot_doc", None)
    if doc is None:
        doc = inspect.getdoc(member.function)
    return doc or ""


def type_overrides(member: ClientMember) -> dict[str, Any] | None:
    """``@keyword(types=...)``: a dict replaces types by name; None disables conversion."""
    if not hasattr(member.function, "robot_types"):
        return {}
    types = member.function.robot_types
    if types is None:
        return None
    if isinstance(types, dict):
        return dict(types)
    names = [p for p in inspect.signature(member.function).parameters if p != "self"]
    return {name: tp for name, tp in zip(names, types, strict=False) if tp is not None}


def matches(name: str, patterns: Iterable[str] | None) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns or ())
