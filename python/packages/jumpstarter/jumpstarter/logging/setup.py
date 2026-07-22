"""Logging setup for Jumpstarter services.

Configures structlog in bridge mode to process stdlib logging records
into structured JSON output (for production) or colored text (for development).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import structlog


def _add_component(logger, method_name, event_dict):
    """Structlog processor that adds the component field if set."""
    if _component_var is None:
        return event_dict
    component = _component_var.get()
    if component and "component" not in event_dict:
        event_dict["component"] = component
    return event_dict


_component_var: _ComponentVar | None = None


class _ComponentVar:
    """Simple holder for the component name (set once at startup)."""

    def __init__(self, value: str):
        self._value = value

    def get(self) -> str:
        return self._value


def setup_logging(
    component: str,
    log_format: str = "auto",
    level: int = logging.INFO,
) -> None:
    """Configure structured logging for a Jumpstarter service.

    This sets up structlog in bridge mode: all existing stdlib logger.info() calls
    are automatically processed through structlog's processor chain and rendered
    as either JSON (production) or colored text (development).

    Args:
        component: Service component name (e.g. "exporter", "controller", "router").
            Added to every log line as the "component" field.
        log_format: Output format selection:
            - "auto": JSON if stderr is not a TTY, colored text if it is
            - "json": Always JSON output
            - "text": Always human-readable colored text
        level: Root logging level (default: INFO).
    """
    global _component_var
    _component_var = _ComponentVar(component)

    use_json = _should_use_json(log_format)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _add_component,
        structlog.stdlib.ExtraAdder(),
        structlog.processors.TimeStamper(fmt="iso", key="ts"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if use_json:
        formatter = structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                _rename_event_to_msg,
                structlog.processors.JSONRenderer(),
            ],
            foreign_pre_chain=shared_processors,
        )
    else:
        formatter = structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(),
            ],
            foreign_pre_chain=shared_processors,
        )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    ns = _detect_namespace()
    if ns:
        from jumpstarter.logging.context import set_persistent_log_context
        set_persistent_log_context(namespace=ns)


def _detect_namespace() -> str | None:
    """Detect the Kubernetes namespace from environment or service account file."""
    ns = os.environ.get("NAMESPACE") or os.environ.get("POD_NAMESPACE")
    if ns:
        return ns
    sa_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
    if sa_path.exists():
        return sa_path.read_text().strip()
    return None


def _rename_event_to_msg(logger, method_name, event_dict):
    """Rename structlog's 'event' key to 'msg' for JEP-0013 compliance."""
    if "event" in event_dict:
        event_dict["msg"] = event_dict.pop("event")
    return event_dict


def _should_use_json(log_format: str) -> bool:
    """Determine whether to use JSON output based on format preference."""
    if log_format == "json":
        return True
    if log_format == "text":
        return False
    # "auto" mode: JSON when stderr is not a TTY
    return not sys.stderr.isatty()
