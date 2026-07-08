"""Log context management using contextvars.

Provides correlation field propagation through async call stacks.
Fields set via set_log_context() are automatically injected into all log
records within the same async context by structlog's contextvars integration.
"""

from __future__ import annotations

import structlog


def set_log_context(**fields: str) -> None:
    """Set correlation fields for the current async context.

    Fields are merged with any existing context (new values override).
    These fields will appear in all subsequent log records from this
    async context until cleared.

    Common fields: lease_id, client, exporter, operation, result, driver_type
    """
    structlog.contextvars.bind_contextvars(**fields)


def clear_log_context() -> None:
    """Clear all correlation fields from the current async context."""
    structlog.contextvars.clear_contextvars()


def unbind_log_context(*keys: str) -> None:
    """Remove specific fields from the current async context."""
    structlog.contextvars.unbind_contextvars(*keys)
