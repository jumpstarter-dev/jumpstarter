"""Driver-facing telemetry: counters, histograms, and gauges per driver.

The dependency arrow is ``driver -> metrics``. Nothing in this module imports
``jumpstarter.driver``; the ``driver_type`` context lives here and the base class
imports it, which avoids the ``driver/__init__`` cycle.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal

from prometheus_client.validation import METRIC_LABEL_NAME_RE, METRIC_NAME_RE

from .definitions import (
    MetricDefinition,
    driver_metric_name,
    prometheus_name,
    reserved_suffix,
)
from .registry import (
    exemplars_from_log_context,
    exporter_from_log_context,
    filter_exemplars,
    get_registry,
)

MetricType = Literal["counter", "histogram", "gauge"]

_RESERVED_LABEL_NAMES = frozenset({"exporter", "driver_type"})
_LABEL_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_INVALID_LABEL_VALUE = "other"

_DRIVER_TYPE_OVERRIDE: ContextVar[str | None] = ContextVar(
    "jumpstarter_driver_type_override", default=None
)


def current_driver_type_override() -> str | None:
    """Return the active ``driver_type`` override, if any."""
    return _DRIVER_TYPE_OVERRIDE.get()


def _sanitize_namespace(namespace: str) -> str:
    sanitized = re.sub(r"[^a-z0-9_]", "_", namespace.strip().lower())
    return sanitized or "driver"


class DriverMetrics:
    """Telemetry handle for one driver instance.

    Created per access from ``Driver.metrics``; it holds no driver reference and
    resolves the registry at use time, so a registry reset never strands a live
    driver.
    """

    def __init__(
        self,
        *,
        namespace: str,
        driver_type: Callable[[], str],
        logger: Any,
        exporter_default: str = "unknown",
        package: str = "",
        source: str = "",
    ) -> None:
        self.namespace = _sanitize_namespace(namespace)
        self._driver_type = driver_type
        self._logger = logger
        self._exporter_default = exporter_default
        self._package = package
        self._source = source
        self._bound_cache: dict[tuple[Any, ...], DriverMetric] = {}
        self._recording_failures: set[str] = set()

    # -- declaration -----------------------------------------------------

    def counter(
        self,
        name: str,
        help: str,
        *,
        labels: Sequence[str] = (),
    ) -> DriverMetric:
        return self._declare("counter", name, help, labels, None)

    def histogram(
        self,
        name: str,
        help: str,
        *,
        labels: Sequence[str] = (),
        buckets: Sequence[float] | None = None,
    ) -> DriverMetric:
        return self._declare("histogram", name, help, labels, buckets)

    def gauge(
        self,
        name: str,
        help: str,
        *,
        labels: Sequence[str] = (),
    ) -> DriverMetric:
        return self._declare("gauge", name, help, labels, None)

    def _declare(
        self,
        metric_type: MetricType,
        name: str,
        help: str,
        labels: Sequence[str],
        buckets: Sequence[float] | None,
    ) -> DriverMetric:
        short_name = self._validate_short_name(name)
        label_names = self._validate_labels(labels)
        full_name = driver_metric_name(self.namespace, short_name, metric_type)
        definition = MetricDefinition(
            name=full_name,
            short_name=short_name,
            namespace=self.namespace,
            type=metric_type,
            help=help,
            labels=label_names,
            buckets=tuple(buckets) if buckets is not None else None,
            package=self._package,
            source=self._source,
        )

        def factory() -> Any:
            return self._create_instrument(definition)

        instrument = get_registry().register_driver_metric(definition, factory)
        return self._bound(definition, instrument, {}, None)

    def _create_instrument(self, definition: MetricDefinition) -> Any:
        labelnames = ("exporter", "driver_type", *definition.labels)
        name = prometheus_name(definition.namespace, definition.short_name)
        registry = get_registry().collector_registry

        # Imported lazily so this module stays cheap to import in the driver
        # base class path.
        from prometheus_client import Counter, Gauge, Histogram

        if definition.type == "counter":
            return Counter(name, definition.help, labelnames, registry=registry)
        if definition.type == "histogram":
            if definition.buckets is None:
                return Histogram(name, definition.help, labelnames, registry=registry)
            return Histogram(
                name,
                definition.help,
                labelnames,
                buckets=definition.buckets,
                registry=registry,
            )
        return Gauge(name, definition.help, labelnames, registry=registry)

    @staticmethod
    def _validate_short_name(name: str) -> str:
        if not name:
            raise ValueError("metric name must not be empty")
        suffix = reserved_suffix(name)
        if suffix is not None:
            raise ValueError(
                f"metric name {name!r} must not end with {suffix!r}; "
                "prometheus_client appends it automatically"
            )
        if not METRIC_NAME_RE.match(name):
            raise ValueError(
                f"invalid metric name {name!r}: must match {METRIC_NAME_RE.pattern}"
            )
        return name

    @staticmethod
    def _validate_labels(labels: Sequence[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        for label in labels:
            if label in _RESERVED_LABEL_NAMES:
                raise ValueError(f"label {label!r} is reserved")
            if not METRIC_LABEL_NAME_RE.match(label):
                raise ValueError(f"invalid label name {label!r}")
            if label in seen:
                raise ValueError(f"duplicate label {label!r}")
            seen.add(label)
        return tuple(labels)

    # -- binding ---------------------------------------------------------

    def _bound(
        self,
        definition: MetricDefinition,
        instrument: Any,
        bound: Mapping[str, str],
        explicit_driver_type: str | None,
    ) -> DriverMetric:
        key = (definition.name, tuple(sorted(bound.items())), explicit_driver_type)
        cached = self._bound_cache.get(key)
        if cached is not None:
            return cached
        handle = DriverMetric(self, definition, instrument, dict(bound), explicit_driver_type)
        self._bound_cache[key] = handle
        return handle

    def _bind_labels(
        self,
        definition: MetricDefinition,
        instrument: Any,
        bound: Mapping[str, str],
        explicit_driver_type: str | None,
        kwargs: Mapping[str, Any],
    ) -> DriverMetric:
        merged = dict(bound)
        driver_type = explicit_driver_type
        for label, value in kwargs.items():
            if label == "exporter":
                raise ValueError("label 'exporter' is reserved and cannot be set by a driver")
            if label == "driver_type":
                driver_type = str(value)
                continue
            if label not in definition.labels:
                raise ValueError(
                    f"label {label!r} is not declared on {definition.name}; "
                    f"declared labels: {definition.labels}"
                )
            merged[label] = self._validated_value(definition.name, label, value)
        return self._bound(definition, instrument, merged, driver_type)

    def _validated_value(self, metric_name: str, label: str, value: Any) -> str:
        text = "" if value is None else str(value)
        if _LABEL_VALUE_RE.match(text):
            return text
        if get_registry().first_invalid_label_warning(metric_name, label):
            self._warn_once(
                "Invalid driver metric label value replaced",
                metric=metric_name,
                label=label,
                value=text[:64],
            )
        return _INVALID_LABEL_VALUE

    # -- resolution ------------------------------------------------------

    def resolve_driver_type(self) -> str:
        return current_driver_type_override() or self._driver_type()

    def resolve_exporter(self) -> str:
        return exporter_from_log_context(default=self._exporter_default)

    @contextmanager
    def driver_type(self, value: str) -> Generator[None]:
        """Attribute observations in this block to ``value``.

        Uses a ``ContextVar`` so concurrent asyncio tasks do not leak categories
        into each other. The same resolver feeds driver metrics, events, and the
        generic ``DriverCall`` metrics.
        """
        token = _DRIVER_TYPE_OVERRIDE.set(value)
        try:
            yield
        finally:
            _DRIVER_TYPE_OVERRIDE.reset(token)

    # -- failure isolation ----------------------------------------------

    def _warn_once(self, message: str, **extra: Any) -> None:
        try:
            self._logger.warning(message, extra=extra)
        except Exception:  # noqa: BLE001, S110 - telemetry must never raise
            pass

    def _recording_failed(self, metric_name: str, exc: BaseException) -> None:
        if metric_name in self._recording_failures:
            return
        self._recording_failures.add(metric_name)
        self._warn_once(
            "Failed to record driver metric",
            metric=metric_name,
            error=type(exc).__name__,
        )


class DriverMetric:
    """A metric family bound to a driver and, optionally, to label values."""

    def __init__(
        self,
        owner: DriverMetrics,
        definition: MetricDefinition,
        instrument: Any,
        bound: Mapping[str, str],
        explicit_driver_type: str | None,
    ) -> None:
        self._owner = owner
        self._definition = definition
        self._instrument = instrument
        self._bound = dict(bound)
        self._explicit_driver_type = explicit_driver_type

    @property
    def definition(self) -> MetricDefinition:
        return self._definition

    @property
    def child(self) -> Any:
        """The underlying ``prometheus_client`` child, for third-party libraries."""
        return self._prometheus_child()

    def labels(self, **kwargs: Any) -> DriverMetric:
        """Return a handle bound to additional label values."""
        return self._owner._bind_labels(
            self._definition,
            self._instrument,
            self._bound,
            self._explicit_driver_type,
            kwargs,
        )

    def inc(self, amount: float = 1.0, *, exemplar: dict[str, str] | None = None) -> None:
        self._record("inc", amount, exemplar)

    def observe(self, value: float, *, exemplar: dict[str, str] | None = None) -> None:
        self._record("observe", value, exemplar)

    def set(self, value: float) -> None:
        self._record("set", value, None)

    # -- internals -------------------------------------------------------

    def _instrument_for_use(self) -> Any:
        return get_registry().driver_instrument(
            self._definition,
            lambda: self._owner._create_instrument(self._definition),
        )

    def _prometheus_child(self) -> Any:
        labels = {
            "exporter": _validated_exporter(
                self._owner,
                self._definition,
                self._owner.resolve_exporter(),
            ),
            "driver_type": self._owner._validated_value(
                self._definition.name,
                "driver_type",
                self._explicit_driver_type or self._owner.resolve_driver_type(),
            ),
            **self._bound,
        }
        return self._instrument_for_use().labels(**labels)

    def _exemplar(self, exemplar: dict[str, str] | None) -> dict[str, str] | None:
        if exemplar is not None:
            return filter_exemplars(exemplar)
        return exemplars_from_log_context()

    def _record(self, verb: str, value: float, exemplar: dict[str, str] | None) -> None:
        try:
            child = self._prometheus_child()
            if verb == "inc":
                child.inc(value, exemplar=self._exemplar(exemplar))
            elif verb == "observe":
                child.observe(value, exemplar=self._exemplar(exemplar))
            else:
                child.set(value)
        except Exception as exc:  # noqa: BLE001 - never raise into the RPC path
            self._owner._recording_failed(self._definition.name, exc)


def _validated_exporter(owner: DriverMetrics, definition: MetricDefinition, value: str) -> str:
    """Exporter names come from cluster identity; still bound the value."""
    return owner._validated_value(definition.name, "exporter", value)
