"""Tests for the workspace driver-telemetry check."""

from __future__ import annotations

import json
from pathlib import Path

from jumpstarter.driver import telemetry_check


def _package(root: Path, slug: str, driver_source: str) -> telemetry_check.DriverPackage:
    path = root / "python" / "packages" / f"jumpstarter-driver-{slug}"
    (path / f"jumpstarter_driver_{slug}").mkdir(parents=True)
    (path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (path / f"jumpstarter_driver_{slug}" / "driver.py").write_text(driver_source)
    return telemetry_check.DriverPackage(slug=slug, path=path)


def _baseline(root: Path, entries: list[str]) -> None:
    path = root / telemetry_check._BASELINE_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(["# header", *entries]) + "\n")


DRIVER_WITH_TYPE = '''
class Power:
    driver_type = "power"

    def __init__(self):
        self.metrics.counter("cycles", "Power cycles", labels=("result",))
        self.metrics.histogram("settle", "Settle", buckets=(1, 2))
'''


def test_scan_package_reads_type_and_metrics(tmp_path):
    package = _package(tmp_path, "power", DRIVER_WITH_TYPE)

    types, metrics = telemetry_check.scan_package(package)

    assert types == {"power"}
    assert [(m.metric_type, m.short_name, m.labels, m.buckets) for m in metrics] == [
        ("counter", "cycles", ("result",), None),
        ("histogram", "settle", (), (1.0, 2.0)),
    ]


def test_scan_ignores_test_files(tmp_path):
    package = _package(tmp_path, "power", "class Power:\n    driver_type = 'power'\n")
    (package.path / "jumpstarter_driver_power" / "driver_test.py").write_text(
        "class Fake:\n    driver_type = 'not-real'\n"
    )

    types, _ = telemetry_check.scan_package(package)
    assert types == {"power"}


def test_guard_accepts_baseline_entry(tmp_path):
    package = _package(tmp_path, "sdwire", "class Sdwire:\n    pass\n")
    errors = telemetry_check.check_guard([package], {"sdwire"})
    assert errors == []


def test_guard_rejects_unlisted_missing_type(tmp_path):
    package = _package(tmp_path, "sdwire", "class Sdwire:\n    pass\n")
    errors = telemetry_check.check_guard([package], set())
    assert "sdwire: no driver_type and not listed in the baseline" in errors


def test_guard_rejects_stale_baseline_entry(tmp_path):
    package = _package(tmp_path, "power", DRIVER_WITH_TYPE)
    errors = telemetry_check.check_guard([package], {"power"})
    assert any("stale baseline entry" in error for error in errors)


def test_guard_rejects_undocumented_type(tmp_path):
    package = _package(tmp_path, "power", "class Power:\n    driver_type = 'made-up'\n")
    errors = telemetry_check.check_guard([package], set())
    assert any("undocumented driver_type" in error for error in errors)


def test_catalog_reports_added_removed_and_changed(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps({"metrics": [{"name": "old", "help": "h"}, {"name": "same", "help": "h"}]})
    )
    rows = [{"name": "same", "help": "changed"}, {"name": "new", "help": "h"}]

    errors = telemetry_check.check_catalog(catalog, rows)

    assert "catalog.json: removed old" in errors
    assert "catalog.json: added new" in errors
    assert "catalog.json: changed same" in errors


def test_write_catalog_is_sorted_and_complete(tmp_path):
    catalog = tmp_path / "catalog.json"
    rows = [
        {"name": "jumpstarter_operations_total", "package": "jumpstarter"},
        {"name": "jumpstarter_driver_power_cycles_total", "package": "jumpstarter-driver-power"},
    ]
    telemetry_check.write_catalog(catalog, rows)

    payload = json.loads(catalog.read_text())
    assert payload["metrics"] == rows
    assert payload["driver_type_allowlist"] == list(telemetry_check.DOCUMENTED_DRIVER_TYPES)


def test_go_authorities_must_agree(tmp_path):
    telemetry = tmp_path / "controller/deploy/operator/internal/controller/jumpstarter"
    service = tmp_path / "controller/internal/service"
    types_dir = tmp_path / "controller/deploy/operator/api/v1alpha1"
    for path in (telemetry, service, types_dir):
        path.mkdir(parents=True)

    entries = "".join(f'"{value}",' for value in telemetry_check.DOCUMENTED_DRIVER_TYPES)
    (telemetry / "telemetry.go").write_text(
        f"var defaultTelemetryDriverTypeEnum = []string{{{entries}}}\n"
    )
    (service / "metrics_merge.go").write_text(f"var DefaultDriverTypeEnum = []string{{{entries}}}\n")
    (types_dir / "jumpstarter_types.go").write_text(
        f'// +kubebuilder:default={{{entries.rstrip(",")}}}\nDriverTypeEnum []string\n'
    )

    assert telemetry_check.check_go_authorities(tmp_path) == []

    (service / "metrics_merge.go").write_text('var DefaultDriverTypeEnum = []string{"power"}\n')
    errors = telemetry_check.check_go_authorities(tmp_path)
    assert any("metrics_merge.go" in error for error in errors)


def test_docs_contract_requires_page_and_categories(tmp_path):
    errors = telemetry_check.check_docs(tmp_path)
    assert any("missing" in error for error in errors)

    page = tmp_path / telemetry_check._DOCS_RELATIVE
    page.parent.mkdir(parents=True)
    page.write_text(" ".join((*telemetry_check._DOC_REQUIRED, *telemetry_check.DOCUMENTED_DRIVER_TYPES)))
    assert telemetry_check.check_docs(tmp_path) == []
