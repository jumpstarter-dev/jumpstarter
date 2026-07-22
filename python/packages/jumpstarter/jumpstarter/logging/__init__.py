"""Structured logging infrastructure for Jumpstarter.

This module provides structured JSON logging for long-running services (exporter,
controller, router) while keeping human-readable output for interactive CLI use.

Uses structlog in "bridge mode" - wrapping Python's stdlib logging so that existing
logger.info() calls are automatically rendered as structured JSON without any
call-site changes.
"""

from jumpstarter.logging.context import (
    clear_log_context,
    set_log_context,
    set_persistent_log_context,
    unbind_log_context,
)
from jumpstarter.logging.setup import setup_logging

__all__ = [
    "clear_log_context",
    "set_log_context",
    "set_persistent_log_context",
    "setup_logging",
    "unbind_log_context",
]
