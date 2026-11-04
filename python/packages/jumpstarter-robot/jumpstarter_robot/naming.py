"""Keyword and prefix naming.

Keyword names are the words of a driver's tree path followed by the words of
the method: ``dutlink.power`` + ``cycle`` is ``Dutlink Power Cycle``. The
prefix is always the full path; an alias is the only way to choose another.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_SEPARATORS = re.compile(r"[\s_.\-/]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def words(*parts: str) -> list[str]:
    """Split identifiers into capitalized words: ``("dut_link", "powerOn")`` → Dut Link Power On."""
    result = []
    for part in parts:
        for chunk in _SEPARATORS.split(part):
            for word in _CAMEL.split(chunk):
                if word:
                    result.append(word[0].upper() + word[1:])
    return result


def title(*parts: str) -> str:
    return " ".join(words(*parts))


def normalize(name: str) -> str:
    """Robot Framework's keyword name matching: case, spaces and underscores are ignored."""
    return name.lower().replace(" ", "").replace("_", "")


def split_path(path: str | Sequence[str]) -> tuple[str, ...]:
    if isinstance(path, str):
        return tuple(segment for segment in path.split(".") if segment)
    return tuple(path)


def keyword_name(prefix: str, method_words: str) -> str:
    return f"{prefix} {method_words}".strip() if prefix else method_words


DEFAULT_SESSION = "default"


def session_tag(session: str) -> str:
    """A session name as it appears in variable names: ``pixel-7`` → ``PIXEL_7``."""
    return "".join(c if c.isalnum() else "_" for c in session.upper())


def session_variable(variable: str, session: str) -> str:
    """The resource variable for ``session``: ``JMP_SELECTOR``, or ``JMP_ANDROID_SELECTOR``.

    Robot Framework keeps the first value of a variable, so two client
    resources in one suite need their own names or the second session would
    use the first one's exporter.
    """
    if session == DEFAULT_SESSION:
        return variable
    return variable.replace("JMP_", f"JMP_{session_tag(session)}_", 1)
