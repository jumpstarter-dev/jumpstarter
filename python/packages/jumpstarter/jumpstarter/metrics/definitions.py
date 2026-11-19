"""Shared metric definitions for core and driver instruments.

Kept separate from both ``registry`` and the driver-facing module so the import
arrow stays ``driver -> metrics``: ``registry`` imports this module, and nothing
here imports ``jumpstarter.driver`` (which would cycle through
``driver/__init__.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DRIVER_METRIC_PREFIX = "jumpstarter_driver_"

# Suffixes prometheus_client appends itself; a driver must not supply them.
_RESERVED_NAME_SUFFIXES = ("_total", "_bucket", "_sum", "_count", "_info", "_created")


class MetricConflictError(ValueError):
    """A metric family was redeclared with a different definition."""


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    """One metric family, as registered and as published in the catalog."""

    name: str
    short_name: str
    namespace: str
    type: str
    help: str
    labels: tuple[str, ...] = ()
    buckets: tuple[float, ...] | None = None
    package: str = ""
    source: str = ""

    def identity(self) -> tuple[Any, ...]:
        """Fields that must match when the same family is declared twice."""
        return (self.type, self.help, self.labels, self.buckets)


def family_name(metric: Any) -> str:
    """Prometheus family name for a collected metric.

    ``prometheus_client`` OpenMetrics ``collect()`` strips ``_total`` from a
    counter family name. The hub encoder treats a counter without that suffix
    as ``unknown``, and text-path snapshots already use the ``_total`` name, so
    mixed exporters would otherwise fail to merge.

    ``Info`` collectors use type ``info`` and a family name without ``_info``;
    OpenMetrics appends that suffix, so the sidecar does the same.
    """
    name = metric.name
    if metric.type == "counter" and not name.endswith("_total"):
        return f"{name}_total"
    if metric.type == "info" and not name.endswith("_info"):
        return f"{name}_info"
    return name


def driver_metric_name(namespace: str, short_name: str, metric_type: str) -> str:
    """Full family name for a driver instrument, matching ``family_name``."""
    base = f"{DRIVER_METRIC_PREFIX}{namespace}_{short_name}"
    if metric_type == "counter" and not base.endswith("_total"):
        return f"{base}_total"
    return base


def prometheus_name(namespace: str, short_name: str) -> str:
    """Name handed to prometheus_client (counters omit the ``_total`` suffix)."""
    return f"{DRIVER_METRIC_PREFIX}{namespace}_{short_name}"


def reserved_suffix(short_name: str) -> str | None:
    """Return the reserved suffix a driver-supplied name ends with, if any."""
    for suffix in _RESERVED_NAME_SUFFIXES:
        if short_name.endswith(suffix):
            return suffix
    return None


def conflict_message(definition: MetricDefinition, existing: MetricDefinition) -> str:
    """Explain the first differing field between two declarations."""
    fields = (
        ("type", existing.type, definition.type),
        ("help", existing.help, definition.help),
        ("labels", existing.labels, definition.labels),
        ("buckets", existing.buckets, definition.buckets),
    )
    for field, old, new in fields:
        if old != new:
            return f"{definition.name} redefined: {field} was {old!r}, now {new!r}"
    return f"{definition.name} redefined with a conflicting definition"
