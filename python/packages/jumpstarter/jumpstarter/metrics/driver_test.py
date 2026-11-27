"""Driver-facing metrics API tests (JEP-0013 Phase 4)."""

from __future__ import annotations

import logging

import pytest
import structlog

from jumpstarter.metrics import DriverMetrics, get_registry
from jumpstarter.metrics._testing import reset_registry_for_tests
from jumpstarter.metrics.definitions import MetricConflictError
from jumpstarter.metrics.families import scrape_response_from_registry
from jumpstarter.metrics.registry import MetricsRegistry

CORE_FAMILIES = 5


@pytest.fixture()
def metrics(fresh_registry) -> DriverMetrics:
    return DriverMetrics(
        namespace="fake",
        driver_type=lambda: "power",
        logger=logging.getLogger("test.driver.metrics"),
        exporter_default="fallback",
        package="jumpstarter-test",
        source="test:fake",
    )


def _family(registry, name: str):
    return {family.name: family for family in scrape_response_from_registry(registry).families}[name]


def _sample(registry, series: str, **labels):
    for family in registry.collector_registry.collect():
        for sample in family.samples:
            if sample.name != series:
                continue
            if all(sample.labels.get(key) == value for key, value in labels.items()):
                return sample
    return None


# -- naming and validation -------------------------------------------------


def test_counter_gets_total_suffix_in_catalog_and_scrape(metrics, fresh_registry):
    metrics.counter("cycles", "Cycles").inc()

    definition = fresh_registry.driver_catalog()[0]
    assert definition.name == "jumpstarter_driver_fake_cycles_total"
    assert definition.short_name == "cycles"
    assert definition.type == "counter"

    # The encoder name and the catalog name agree (D12).
    assert _family(fresh_registry, "jumpstarter_driver_fake_cycles_total").name == definition.name
    assert _sample(fresh_registry, "jumpstarter_driver_fake_cycles_total", exporter="fallback")


def test_histogram_keeps_base_name(metrics, fresh_registry):
    metrics.histogram("settle_seconds", "Settle", buckets=(1, 2)).observe(1.5)

    assert fresh_registry.driver_catalog()[0].name == "jumpstarter_driver_fake_settle_seconds"
    assert _sample(fresh_registry, "jumpstarter_driver_fake_settle_seconds_bucket", le="2.0")


@pytest.mark.parametrize(
    "name",
    ["", "bad name", "cycles_total", "x_bucket", "x_sum", "x_count", "_created"],
)
def test_invalid_short_names_raise(metrics, name):
    with pytest.raises(ValueError):
        metrics.counter(name, "Help")


@pytest.mark.parametrize("labels", [("exporter",), ("driver_type",), ("bad-name",), ("a", "a")])
def test_invalid_label_declarations_raise(metrics, labels):
    with pytest.raises(ValueError):
        metrics.counter("cycles", "Cycles", labels=labels)


def test_namespace_is_sanitized():
    handle = DriverMetrics(
        namespace="Jumpstarter-Driver.Power",
        driver_type=lambda: "power",
        logger=logging.getLogger("test.driver.metrics"),
    )
    handle.counter("cycles", "Cycles")
    assert get_registry().driver_catalog()[0].namespace == "jumpstarter_driver_power"


# -- idempotence and conflicts --------------------------------------------


def test_repeated_declaration_returns_same_handle(metrics, fresh_registry):
    first = metrics.counter("cycles", "Cycles")
    second = metrics.counter("cycles", "Cycles")

    assert first is second
    assert len(fresh_registry.driver_catalog()) == 1


def test_conflicting_type_names_the_field(metrics):
    metrics.histogram("h", "Help")
    with pytest.raises(MetricConflictError, match=r"type was 'histogram', now 'gauge'"):
        metrics.gauge("h", "Help")


def test_conflicting_help_names_the_field(metrics):
    metrics.counter("h", "Help")
    with pytest.raises(MetricConflictError, match=r"help was 'Help', now 'Other'"):
        metrics.counter("h", "Other")


def test_conflicting_labels_names_the_field(metrics):
    metrics.counter("h", "Help")
    with pytest.raises(MetricConflictError, match=r"labels was \(\), now \('result',\)"):
        metrics.counter("h", "Help", labels=("result",))


def test_conflicting_buckets_names_the_field(metrics):
    metrics.histogram("h", "Help", buckets=(1.0,))
    with pytest.raises(MetricConflictError, match=r"buckets was \(1\.0,\), now \(2\.0,\)"):
        metrics.histogram("h", "Help", buckets=(2.0,))


# -- labels ---------------------------------------------------------------


def test_exporter_label_is_reserved(metrics):
    with pytest.raises(ValueError):
        metrics.counter("cycles", "Cycles").labels(exporter="someone-else")


def test_undeclared_label_is_rejected(metrics):
    with pytest.raises(ValueError):
        metrics.counter("cycles", "Cycles").labels(result="success")


def test_driver_type_can_be_overridden_per_call(metrics, fresh_registry):
    metrics.counter("ops", "Ops").labels(driver_type="storage").inc()

    assert _sample(fresh_registry, "jumpstarter_driver_fake_ops_total", driver_type="storage")


def test_driver_type_context_manager(metrics, fresh_registry):
    handle = metrics.counter("ops", "Ops")
    with metrics.driver_type("storage"):
        handle.inc()
    handle.inc()

    assert (
        _sample(
            fresh_registry,
            "jumpstarter_driver_fake_ops_total",
            driver_type="storage",
            exporter="fallback",
        )
        is not None
    )
    values = [
        sample.value
        for family in fresh_registry.collector_registry.collect()
        for sample in family.samples
        if sample.name == "jumpstarter_driver_fake_ops_total"
    ]
    assert sorted(values) == [1.0, 1.0]


# -- record-time exporter (D11) -------------------------------------------


def test_exporter_is_resolved_at_record_time(metrics, fresh_registry):
    handle = metrics.counter("late", "Late")
    handle.inc()
    with structlog.contextvars.bound_contextvars(exporter="lab-01"):
        handle.inc()

    assert _sample(fresh_registry, "jumpstarter_driver_fake_late_total", exporter="fallback") is not None
    assert _sample(fresh_registry, "jumpstarter_driver_fake_late_total", exporter="lab-01") is not None


# -- registry reset (A3/T4) -----------------------------------------------


def test_held_handle_records_into_the_current_registry(metrics, fresh_registry):
    handle = metrics.counter("cycles", "Cycles")
    handle.inc()

    reset_registry_for_tests()
    handle.inc()

    assert _sample(get_registry(), "jumpstarter_driver_fake_cycles_total", exporter="fallback") is not None
    assert _sample(get_registry(), "jumpstarter_driver_fake_cycles_total").value == 1.0


# -- cardinality (FR-6) ---------------------------------------------------


def test_invalid_label_value_becomes_other_and_warns_once(metrics, caplog):
    caplog.set_level(logging.WARNING, logger="test.driver.metrics")
    first = metrics.counter("bad", "Bad", labels=("device",))
    second = DriverMetrics(
        namespace="fake",
        driver_type=lambda: "power",
        logger=logging.getLogger("test.driver.metrics"),
        exporter_default="fallback",
    ).counter("bad", "Bad", labels=("device",))

    first.labels(device="/dev/ttyUSB0").inc()
    second.labels(device="/dev/ttyUSB1").inc()

    sample = _sample(get_registry(), "jumpstarter_driver_fake_bad_total")
    assert sample.labels["device"] == "other"
    assert sample.value == 2.0
    warnings = [r for r in caplog.records if "Invalid driver metric label value" in r.message]
    assert len(warnings) == 1


def test_allowed_value_characters_pass(metrics, fresh_registry):
    metrics.counter("ok", "Ok", labels=("mode",)).labels(mode="a-b_c.d:1").inc()
    assert _sample(fresh_registry, "jumpstarter_driver_fake_ok_total", mode="a-b_c.d:1") is not None


# -- failure isolation and cost ------------------------------------------


def test_recording_failure_is_isolated(metrics, caplog):
    caplog.set_level(logging.WARNING, logger="test.driver.metrics")
    handle = metrics.counter("boom", "Boom")

    class Broken:
        def inc(self, *args, **kwargs):
            raise RuntimeError("registry exploded")

    handle._instrument_for_use = lambda: Broken()  # type: ignore[method-assign]

    handle.inc()
    handle.inc()

    warnings = [r for r in caplog.records if "Failed to record driver metric" in r.message]
    assert len(warnings) == 1


def test_unused_driver_registers_no_families(fresh_registry):
    DriverMetrics(namespace="fake", driver_type=lambda: "power", logger=logging.getLogger("x"))
    assert len(list(fresh_registry.collector_registry.collect())) == CORE_FAMILIES


def test_record_path_does_not_register(metrics, monkeypatch):
    calls: list[str] = []
    original = MetricsRegistry.register_driver_metric

    def spy(self, definition, factory):
        calls.append(definition.name)
        return original(self, definition, factory)

    monkeypatch.setattr(MetricsRegistry, "register_driver_metric", spy)

    handle = metrics.counter("cycles", "Cycles")
    handle.inc()
    handle.labels().inc()

    assert calls == ["jumpstarter_driver_fake_cycles_total"]


# -- exemplars (FR-7) -----------------------------------------------------


def test_default_exemplar_comes_from_context(metrics):
    with structlog.contextvars.bound_contextvars(lease_id="lease-1", client="ci-bot"):
        metrics.counter("op", "Op").inc()

    family = _family(get_registry(), "jumpstarter_driver_fake_op_total")
    exemplar = {lp.name: lp.value for lp in family.samples[0].exemplar.labels}
    assert exemplar["lease_id"] == "lease-1"
    assert exemplar["client"] == "ci-bot"


def test_explicit_exemplar_wins(metrics):
    with structlog.contextvars.bound_contextvars(lease_id="lease-1"):
        metrics.counter("op", "Op").inc(exemplar={"lease_id": "explicit"})

    family = _family(get_registry(), "jumpstarter_driver_fake_op_total")
    exemplar = {lp.name: lp.value for lp in family.samples[0].exemplar.labels}
    assert exemplar["lease_id"] == "explicit"


def test_histogram_exemplar_survives_on_bucket(metrics):
    with structlog.contextvars.bound_contextvars(lease_id="lease-1"):
        metrics.histogram("settle", "Settle").observe(0.5)

    family = _family(get_registry(), "jumpstarter_driver_fake_settle")
    buckets = [s for s in family.samples if s.name.endswith("_bucket") and s.HasField("exemplar")]
    assert buckets
    assert {lp.name: lp.value for lp in buckets[0].exemplar.labels}["lease_id"] == "lease-1"
