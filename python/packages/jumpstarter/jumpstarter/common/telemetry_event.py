"""Structured event fields for drivers (JEP-0013 DD-2).

The telemetry service enforces the same caps server-side; bounding here keeps a
driver from building a record the service would silently truncate, and keeps
reserved ``LogRecord`` keys out of ``extra`` (which would raise).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

MAX_STRUCTURED_FIELDS = 16
MAX_FIELD_KEY_LENGTH = 64
MAX_FIELD_VALUE_LENGTH = 256

# Every attribute logging.LogRecord already owns (message, msg, args, levelname,
# time, ...). Passing one through ``extra=`` raises KeyError in makeRecord.
RESERVED_FIELD_KEYS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
) | frozenset({"asctime", "message"})


def bounded_structured_fields(fields: Mapping[str, Any] | None) -> tuple[dict[str, str], int]:
    """Return bounded fields and the number dropped.

    Drops reserved keys and ``None`` values, caps the mapping at 16 entries,
    truncates keys to 64 and values to 256 characters, and stops adding entries
    once the cap is reached.
    """
    if not fields:
        return {}, 0

    bounded: dict[str, str] = {}
    dropped = 0
    for key, value in fields.items():
        text_key = str(key)
        if text_key in RESERVED_FIELD_KEYS or value is None:
            dropped += 1
            continue
        if len(bounded) >= MAX_STRUCTURED_FIELDS:
            dropped += 1
            continue
        bounded[text_key[:MAX_FIELD_KEY_LENGTH]] = str(value)[:MAX_FIELD_VALUE_LENGTH]
    return bounded, dropped
