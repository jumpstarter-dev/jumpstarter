"""Exporter-local Prometheus metrics registry (JEP-0013 Phase 2)."""

from __future__ import annotations

import structlog
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.openmetrics.exposition import generate_latest as generate_latest_openmetrics

DEFAULT_EXEMPLAR_KEYS = ("client", "lease_id")

_REGISTRY: MetricsRegistry | None = None


def filter_exemplars(exemplars: dict[str, str] | None) -> dict[str, str] | None:
    """Keep only the JEP default exemplar keys with non-empty values."""
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
    """Process-local CollectorRegistry holding JEP-named exporter series."""

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
            "Errors by class (timeout, device, …).",
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

    @property
    def collector_registry(self) -> CollectorRegistry:
        return self._registry

    def record_operation(
        self,
        *,
        exporter: str,
        operation: str,
        result: str,
        driver_type: str,
        duration_seconds: float,
        exemplars: dict[str, str] | None = None,
        error_type: str | None = None,
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
        direction: str,
        nbytes: int,
        exemplars: dict[str, str] | None = None,
    ) -> None:
        if nbytes <= 0:
            return
        self._stream_bytes.labels(
            exporter=exporter,
            driver_type=driver_type,
            direction=direction,
        ).inc(nbytes, exemplar=filter_exemplars(exemplars))

    def set_active_sessions(self, *, exporter: str, value: float) -> None:
        self._active_sessions.labels(exporter=exporter).set(value)

    def inc_active_sessions(self, *, exporter: str, delta: float = 1.0) -> None:
        self._active_sessions.labels(exporter=exporter).inc(delta)

    def generate_latest(self) -> bytes:
        return generate_latest_openmetrics(self._registry)


def get_registry() -> MetricsRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = MetricsRegistry()
    return _REGISTRY


def reset_registry_for_tests() -> MetricsRegistry:
    """Replace the process-wide registry (tests only)."""
    global _REGISTRY
    _REGISTRY = MetricsRegistry()
    return _REGISTRY
