"""Canonical ``to_jsonable`` normalizer for the FFI value boundary.

Driver-call args (client→host) and driver return values (host→client) cross the FFI seam
as plain JSON; the Rust core then applies the proto-``Value`` codec. This function reproduces
the exact output of the legacy ``serde.encode_value`` path, which was
``pydantic.TypeAdapter(Any).dump_python(value, mode="json")`` — verified byte-for-byte against
it over a corpus (UUID→str, Enum→value, datetime→RFC3339 with ``Z`` for UTC, date/time→ISO,
Decimal→str, Path→str, bytes→UTF-8, non-finite floats→None, tuple/set→list, pydantic
models→``model_dump(mode="json")``). Pure stdlib + an optional ``.model_dump`` duck-type, so
the core driver-dispatch path carries no pydantic dependency of its own.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import PurePath
from typing import Any
from uuid import UUID

__all__ = ["to_jsonable"]


def to_jsonable(value: Any) -> Any:  # noqa: C901 — flat type-dispatch; each branch is one isinstance
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, Enum):  # before (str, int): IntEnum/StrEnum are int/str subclasses
        return to_jsonable(value.value)
    if isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        out = value.isoformat()
        # pydantic mode="json" emits RFC3339 with a trailing Z for UTC, not +00:00.
        return out[:-6] + "Z" if out.endswith("+00:00") else out
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):  # pydantic BaseModel (the driver brings pydantic)
        return to_jsonable(model_dump(mode="json"))
    raise TypeError(f"cannot serialize value of type {type(value)!r} for the FFI value boundary")
