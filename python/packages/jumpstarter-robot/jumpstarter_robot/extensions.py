"""Extra keywords for well-known client classes.

The keyword sets (console, SSH, ADB, iOS, Appium) work on any driver path.
They also register a short, node-prefixed shortcut for the client classes they
recognize, so the ``Driver`` library of ``dut.ssh`` offers ``Dut Ssh Connect``
and IntelliSense shows it next to that driver's own keywords.

Classes are matched by the names in their MRO, so this package never imports a
driver package it does not need.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NodeExtension:
    """A keyword added to the ``Driver`` library of matching nodes.

    ``function(path, session, *args, **kwargs)`` runs it; its remaining
    parameters become the keyword's arguments.
    """

    class_names: frozenset[str]
    method_words: str
    function: Callable[..., Any]
    doc: str = ""
    tags: tuple[str, ...] = ()


_lock = threading.Lock()
_extensions: list[NodeExtension] = []


def register_node_extension(
    class_names: set[str] | frozenset[str],
    method_words: str,
    function: Callable[..., Any],
    *,
    doc: str = "",
    tags: tuple[str, ...] = (),
) -> None:
    with _lock:
        _extensions.append(NodeExtension(frozenset(class_names), method_words, function, doc, tags))


def extensions_for(cls: type) -> list[NodeExtension]:
    _load_builtin()
    names = {klass.__name__ for klass in cls.__mro__} | {f"{k.__module__}.{k.__qualname__}" for k in cls.__mro__}
    with _lock:
        found: dict[str, NodeExtension] = {}
        for extension in _extensions:
            if extension.class_names & names and extension.method_words not in found:
                found[extension.method_words] = extension
        return list(found.values())


_loaded = False


def _load_builtin() -> None:
    """Import the built-in keyword sets so their extensions are registered."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    from . import keywords

    keywords.load_all()
