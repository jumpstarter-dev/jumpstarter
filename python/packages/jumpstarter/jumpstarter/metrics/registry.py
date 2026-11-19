"""Exporter-local Prometheus metrics registry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

import structlog
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.openmetrics.exposition import generate_latest as generate_latest_openmetrics

from .definitions import MetricConflictError, MetricDefinition, conflict_message

DEFAULT_EXEMPLAR_KEYS = ("client", "lease_id")

OperationResult = Literal["success", "failure"]
StreamDirection = Literal["tx", "rx"]
ErrorType = Literal[
    "not_implemented",
    "validation_error",
    "timeout",
    "connection_error",
    "device_error",
    "internal_error",
]


def filter_exemplars(exemplars: dict[str, str] | None) -> dict[str, str] | None:
    """Keep only the default exemplar keys with non-empty values."""
    if not exemplars:
        return None
    filtered = {
        key: str(exemplars[key])
        for key in DEFAULT_EXEMPLAR_KEYS
        if key in exemplars and exemplars[key] is not None and str(exemplars[key]) != ""
    }
    return filtered or None


def exemplars_from_log_context() -> dict[str, str] | None:
    """Build default exemplars from the current structlog contextvars."""
    ctx = structlog.contextvars.get_contextvars()
    return filter_exemplars({key: str(ctx[key]) for key in DEFAULT_EXEMPLAR_KEYS if key in ctx})


def exporter_from_log_context(default: str = "unknown") -> str:
    ctx = structlog.contextvars.get_contextvars()
    value = ctx.get("exporter")
    return str(value) if value else default


class MetricsRegistry:
    """Process-local CollectorRegistry holding exporter metric series."""

    def __init__(self) -> None:
        self._registry = CollectorRegistry()
        self._operations = Counter(
            "jumpstarter_operations_total",
            "Total operations performed.",
            ["exporter", "operation", "result", "driver_type"],
            registry=self._registry,
        )
        self._duration = Histogram(
            "jumpstarter_operation_duration_seconds",
            "Duration of each operation.",
            ["exporter", "operation", "result", "driver_type"],
            registry=self._registry,
        )
        self._errors = Counter(
            "jumpstarter_operation_errors_total",
            "Errors by class (timeout, device, ...).",
            ["exporter", "operation", "driver_type", "error_type"],
            registry=self._registry,
        )
        self._stream_bytes = Counter(
            "jumpstarter_stream_bytes_total",
            "Bytes transferred (tx/rx) on streams.",
            ["exporter", "driver_type", "direction"],
            registry=self._registry,
        )
        self._active_sessions = Gauge(
            "jumpstarter_active_sessions",
            "Currently active lease sessions.",
            ["exporter"],
            registry=self._registry,
        )
        # Driver instruments are registered lazily as drivers declare them.
        # Definitions and instruments are keyed by full family name so a
        # repeated declaration returns the same instrument and a conflicting
        # one raises.
        self._driver_definitions: dict[str, MetricDefinition] = {}
        self._driver_instruments: dict[str, Any] = {}
        self._driver_label_warnings: set[tuple[str, str]] = set()

    @property
    def collector_registry(self) -> CollectorRegistry:
        return self._registry

    def register_driver_metric(
        self,
        definition: MetricDefinition,
        factory: Callable[[], Any],
    ) -> Any:
        """Register (once) and return the prometheus instrument for a family.

        An identical re-declaration returns the existing instrument; any
        difference in type, help, labels, or buckets raises
        ``MetricConflictError``.
        """
        existing = self._driver_definitions.get(definition.name)
        if existing is not None:
            if existing.identity() != definition.identity():
                raise MetricConflictError(conflict_message(definition, existing))
            return self._driver_instruments[definition.name]

        instrument = factory()
        self._driver_definitions[definition.name] = definition
        self._driver_instruments[definition.name] = instrument
        return instrument

    def driver_instrument(
        self,
        definition: MetricDefinition,
        factory: Callable[[], Any],
    ) -> Any:
        """Return the registered instrument, creating it on first use.

        The record path calls this instead of ``register_driver_metric``: an
        existing instrument is one dictionary lookup, and a handle that outlived
        a registry reset re-registers into the current registry.
        """
        existing = self._driver_instruments.get(definition.name)
        if existing is not None:
            return existing
        return self.register_driver_metric(definition, factory)

    def driver_catalog(self) -> list[MetricDefinition]:
        """Registered driver families, sorted for a deterministic catalog."""
        return sorted(self._driver_definitions.values(), key=lambda d: (d.package, d.name))

    def core_catalog(self) -> list[MetricDefinition]:
        """Definitions for the built-in exporter families.

        Uses the instruments' own name/documentation/label metadata so the
        catalog cannot drift from what ``collect()`` exposes. ``_name`` is
        prometheus_client's internal (counters have ``_total`` stripped there).
        """
        specs = (
            (self._operations, "counter"),
            (self._duration, "histogram"),
            (self._errors, "counter"),
            (self._stream_bytes, "counter"),
            (self._active_sessions, "gauge"),
        )
        definitions = []
        for instrument, metric_type in specs:
            base = instrument._name.removesuffix("_total")
            definitions.append(
                MetricDefinition(
                    name=f"{base}_total" if metric_type == "counter" else base,
                    short_name=base,
                    namespace="core",
                    type=metric_type,
                    help=instrument._documentation,
                    labels=tuple(instrument._labelnames),
                    package="jumpstarter",
                    source="jumpstarter.metrics.registry",
                )
            )
        return definitions

    def reset_driver_instruments(self) -> None:
        """Drop every driver instrument, definition, and warning marker."""
        self._driver_definitions.clear()
        self._driver_instruments.clear()
        self._driver_label_warnings.clear()

    def first_invalid_label_warning(self, metric_name: str, label: str) -> bool:
        """True the first time an invalid value is seen for a metric/label."""
        key = (metric_name, label)
        if key in self._driver_label_warnings:
            return False
        self._driver_label_warnings.add(key)
        return True

    def record_operation(
        self,
        *,
        exporter: str,
        operation: str,
        result: OperationResult,
        driver_type: str,
        duration_seconds: float,
        exemplars: dict[str, str] | None = None,
        error_type: ErrorType | None = None,
    ) -> None:
        labels = {
            "exporter": exporter,
            "operation": operation,
            "result": result,
            "driver_type": driver_type,
        }
        exemplar = filter_exemplars(exemplars)
        self._operations.labels(**labels).inc(exemplar=exemplar)
        self._duration.labels(**labels).observe(duration_seconds, exemplar=exemplar)
        if result == "failure" and error_type:
            self._errors.labels(
                exporter=exporter,
                operation=operation,
                driver_type=driver_type,
                error_type=error_type,
            ).inc(exemplar=exemplar)

    def add_stream_bytes(
        self,
        *,
        exporter: str,
        driver_type: str,
        direction: StreamDirection,
        nbytes: int,
        exemplars: dict[str, str] | None = None,
    ) -> None:
        # Zero/negative transfers are not observed (no counter change).
        if nbytes <= 0:
            return
        self._stream_bytes.labels(
            exporter=exporter,
            driver_type=driver_type,
            direction=direction,
        ).inc(nbytes, exemplar=filter_exemplars(exemplars))

    def set_active_sessions(self, *, exporter: str, value: float) -> None:
        self._active_sessions.labels(exporter=exporter).set(value)

    def adjust_active_sessions(self, *, exporter: str, delta: float = 1.0) -> None:
        self._active_sessions.labels(exporter=exporter).inc(delta)

    def generate_latest(self) -> bytes:
        return generate_latest_openmetrics(self._registry)


# Eager singleton: the asyncio serve path and ThreadingHTTPServer scrape
# handler can call get_registry() concurrently; lazy init would race and
# discard counters already recorded on the first instance.
_REGISTRY = MetricsRegistry()


def get_registry() -> MetricsRegistry:
    return _REGISTRY
