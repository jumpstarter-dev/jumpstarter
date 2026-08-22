"""Test-only helpers for exporter metrics (not part of the public API)."""

from __future__ import annotations

from . import registry as _registry
from .registry import MetricsRegistry


def reset_registry_for_tests() -> MetricsRegistry:
    """Replace the process-wide registry (tests only)."""
    _registry._REGISTRY = MetricsRegistry()
    return _registry._REGISTRY
