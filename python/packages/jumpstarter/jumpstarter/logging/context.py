"""Log context management using contextvars.

Provides correlation field propagation through async call stacks.
Fields set via set_log_context() are automatically injected into all log
records within the same async context by structlog's contextvars integration.
"""

from __future__ import annotations

import structlog

_persistent_fields: dict[str, str] = {}


def set_persistent_log_context(**fields: str) -> None:
    """Set fields that survive clear_log_context() (e.g. namespace, component)."""
    _persistent_fields.update(fields)
    structlog.contextvars.bind_contextvars(**fields)


def set_log_context(**fields: str) -> None:
    """Set correlation fields for the current async context.

    Fields are merged with any existing context (new values override).
    These fields will appear in all subsequent log records from this
    async context until cleared.

    Common fields: lease_id, client, exporter, operation, result, driver_type
    """
    structlog.contextvars.bind_contextvars(**fields)


def clear_log_context() -> None:
    """Clear lease-scoped correlation fields, preserving persistent fields (namespace, component)."""
    structlog.contextvars.clear_contextvars()
    if _persistent_fields:
        structlog.contextvars.bind_contextvars(**_persistent_fields)


def unbind_log_context(*keys: str) -> None:
    """Remove specific fields from the current async context."""
    structlog.contextvars.unbind_contextvars(*keys)
