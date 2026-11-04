"""Convert driver results into values Robot Framework tests can use."""

from __future__ import annotations

import dataclasses
import datetime
import enum
import inspect
import itertools
from collections.abc import Callable, Mapping
from pathlib import Path, PurePath
from typing import Any

from pydantic import BaseModel

_DATA = (str, bytes, bytearray, int, float, bool, type(None), enum.Enum, datetime.date, datetime.timedelta, PurePath)


def _is_data(value: Any) -> bool:
    """Whether ``value`` is plain data all the way down, safe to turn into dictionaries.

    A dataclass that holds a live object (a ``BlockingStream`` yielded by
    ``stream()``, a socket, a portal) is returned as it is: copying it into a
    dictionary would break it, and ``dataclasses.asdict`` cannot even copy it.
    """
    if isinstance(value, (*_DATA, BaseModel)):
        return True
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return all(_is_data(getattr(value, f.name)) for f in dataclasses.fields(value))
    if isinstance(value, Mapping):
        return all(_is_data(k) and _is_data(v) for k, v in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return all(_is_data(item) for item in value)
    return False


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _plain(value.model_dump(mode="python"))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def to_robot(value: Any, *, max_items: int = 1000, save_image: Callable[[Any], Path] | None = None) -> Any:
    """Make a driver result convenient in Robot Framework.

    pydantic models and dataclasses become ``DotDict`` so both
    ``${result.stdout}`` and ``${result}[stdout]`` work; generators are read up
    to ``max_items`` and closed, which also cancels the driver-side stream; PIL
    images are saved through ``save_image`` and the path returned.
    """
    from robot.utils import DotDict

    if inspect.isgenerator(value):
        try:
            items = itertools.islice(value, max_items)
            return [to_robot(item, max_items=max_items, save_image=save_image) for item in items]
        finally:
            value.close()
    if save_image is not None and _is_pil_image(value):
        return str(save_image(value))
    if isinstance(value, BaseModel) or (
        dataclasses.is_dataclass(value) and not isinstance(value, type) and _is_data(value)
    ):
        plain = _plain(value)
        return DotDict(plain) if isinstance(plain, dict) else plain
    if isinstance(value, Mapping) and not isinstance(value, DotDict):
        return DotDict(_plain(value))
    if isinstance(value, (list, tuple)):
        return [to_robot(item, max_items=max_items, save_image=save_image) for item in value]
    return value


def _is_pil_image(value: Any) -> bool:
    return type(value).__module__.startswith("PIL.") and hasattr(value, "save")
