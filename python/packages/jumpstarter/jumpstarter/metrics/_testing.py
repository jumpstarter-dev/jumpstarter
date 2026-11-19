"""Test-only helpers for exporter metrics (not part of the public API)."""

from __future__ import annotations

from . import registry as _registry
from .registry import MetricsRegistry


def reset_registry_for_tests() -> MetricsRegistry:
    """Replace the process-wide registry (tests only).

    A fresh registry starts with no driver instruments, so any handle a test
    created before the reset resolves the new registry on its next use instead
    of writing to the dead one.
    """
    registry = MetricsRegistry()
    _registry._REGISTRY = registry
    return registry
