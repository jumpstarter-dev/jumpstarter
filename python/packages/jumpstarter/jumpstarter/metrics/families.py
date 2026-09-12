"""Encode a CollectorRegistry into MetricsFamily protos (JEP-0013 DD-3 sidecar).

The hub merges these families instead of parsing OpenMetrics text, which
prometheus/common cannot do when exemplars are present.
"""

from __future__ import annotations

from typing import Any

from jumpstarter_protocol import telemetry_pb2
from prometheus_client import CollectorRegistry

from .registry import MetricsRegistry

_TYPE_MAP = {
    "counter": telemetry_pb2.METRICS_TYPE_COUNTER,
    "gauge": telemetry_pb2.METRICS_TYPE_GAUGE,
    "histogram": telemetry_pb2.METRICS_TYPE_HISTOGRAM,
    "gaugehistogram": telemetry_pb2.METRICS_TYPE_HISTOGRAM,
    "info": telemetry_pb2.METRICS_TYPE_GAUGE,
    "stateset": telemetry_pb2.METRICS_TYPE_GAUGE,
    "summary": telemetry_pb2.METRICS_TYPE_SUMMARY,
    "untyped": telemetry_pb2.METRICS_TYPE_UNTYPED,
    "unknown": telemetry_pb2.METRICS_TYPE_UNTYPED,
}


def _family_name(metric: Any) -> str:
    """Prometheus family name, including ``_total`` for counters.

    ``prometheus_client`` OpenMetrics ``collect()`` strips ``_total`` from the
    family name. The hub OpenMetrics encoder treats a counter without that
    suffix as ``unknown``, and text-path snapshots already use the ``_total``
    name, so mixed exporters would otherwise fail to merge.

    ``Info`` collectors use type ``info`` and a family name without ``_info``;
    OpenMetrics appends that suffix, so the sidecar does the same.
    """
    name = metric.name
    if metric.type == "counter" and not name.endswith("_total"):
        return f"{name}_total"
    if metric.type == "info" and not name.endswith("_info"):
        return f"{name}_info"
    return name


def _set_unix_timestamp(dest: Any, ts: float) -> None:
    seconds = int(ts)
    nanos = int(round((ts - seconds) * 1_000_000_000))
    if nanos >= 1_000_000_000:
        seconds += 1
        nanos -= 1_000_000_000
    if nanos < 0:
        seconds -= 1
        nanos += 1_000_000_000
    dest.seconds = seconds
    dest.nanos = nanos


def _copy_labels(dest: Any, labels: Any) -> None:
    if not labels:
        return
    for name, val in labels.items():
        if name is None or val is None:
            continue
        lp = dest.add()
        lp.name = str(name)
        lp.value = str(val)


def _exemplar_proto(raw: Any) -> telemetry_pb2.MetricsExemplar | None:
    if not raw:
        return None
    labels = getattr(raw, "labels", None)
    value = getattr(raw, "value", None)
    timestamp = getattr(raw, "timestamp", None)
    if not labels and value is None:
        return None
    ex = telemetry_pb2.MetricsExemplar()
    if value is not None:
        ex.value = float(value)
    _copy_labels(ex.labels, labels)
    if timestamp is not None:
        try:
            _set_unix_timestamp(ex.timestamp, float(timestamp))
        except (TypeError, ValueError):
            pass
    if not ex.labels and ex.value == 0 and not ex.HasField("timestamp"):
        return None
    return ex


def families_from_collector(registry: CollectorRegistry) -> list[telemetry_pb2.MetricsFamily]:
    """Dump ``registry.collect()`` into proto families, including exemplars."""
    out: list[telemetry_pb2.MetricsFamily] = []
    for metric in registry.collect():
        fam = telemetry_pb2.MetricsFamily(
            name=_family_name(metric),
            help=metric.documentation or "",
            type=_TYPE_MAP.get(metric.type, telemetry_pb2.METRICS_TYPE_UNSPECIFIED),
        )
        for sample in metric.samples:
            if sample.name.endswith("_created"):
                continue
            ps = fam.samples.add()
            ps.name = sample.name
            ps.value = float(sample.value)
            _copy_labels(ps.labels, sample.labels)
            ex = _exemplar_proto(getattr(sample, "exemplar", None))
            if ex is not None:
                ps.exemplar.CopyFrom(ex)
        if fam.samples:
            out.append(fam)
    return out


def scrape_response_from_registry(
    registry: MetricsRegistry,
    *,
    include_text: bool = True,
) -> telemetry_pb2.MetricsScrapeResponse:
    """Build a MetricsScrapeResponse from the in-memory registry.

    Structured ``families`` are the scrape payload. ``metrics_text`` remains
    optional OpenMetrics for lab/debug and older hubs.
    """
    resp = telemetry_pb2.MetricsScrapeResponse()
    resp.timestamp.GetCurrentTime()
    resp.families.extend(families_from_collector(registry.collector_registry))
    if include_text:
        resp.metrics_text = registry.generate_latest()
    return resp
