"""Workspace driver-telemetry check (JEP-0013 Phase 4).

Discovers driver packages on disk and parses them with ``ast`` — no imports, so
optional native dependencies and the isolated core test environment do not
matter. Enforces the ``driver_type`` baseline and documented set, builds and
diffs the metric catalog, asserts the docs contract, and checks that the three
Go allowlist authorities agree.

Run ``make driver-telemetry-check`` from ``python/``; set ``UPDATE_CATALOG=1`` to
rewrite the catalog.
"""

from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from jumpstarter.metrics.definitions import MetricDefinition, driver_metric_name

DOCUMENTED_DRIVER_TYPES = (
    "appium",
    "automotive",
    "bluetooth",
    "composite",
    "console",
    "debug",
    "gpio",
    "iosdevice",
    "network",
    "power",
    "serial",
    "shell",
    "storage",
    "testing",
    "video",
)

METRIC_TYPES = ("counter", "histogram", "gauge")

_CATALOG_RELATIVE = Path("docs/source/_static/driver-telemetry-catalog.json")
_DOCS_RELATIVE = Path("docs/source/getting-started/guides/driver-telemetry.md")
_BASELINE_RELATIVE = Path("python/packages/jumpstarter/jumpstarter/driver/_driver_type_baseline.txt")

_DOC_REQUIRED = (
    "self.metrics.counter",
    "self.metrics.histogram",
    "with self.metrics.driver_type",
    "driver-telemetry-catalog.json",
    "_driver_type_baseline.txt",
    "HTTP server",
)


@dataclass(frozen=True, slots=True)
class DriverPackage:
    slug: str
    path: Path


@dataclass(frozen=True, slots=True)
class DeclaredMetric:
    package: str
    namespace: str
    metric_type: str
    short_name: str
    help: str
    labels: tuple[str, ...]
    buckets: tuple[float, ...] | None


def repo_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` until the repository markers are found."""
    current = (start or Path(__file__)).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "python/packages").is_dir() and (candidate / "docs/source").is_dir():
            return candidate
    raise RuntimeError("could not locate the repository root")


def discover_packages(repo: Path) -> list[DriverPackage]:
    packages_dir = repo / "python" / "packages"
    packages = []
    for path in sorted(packages_dir.glob("jumpstarter-driver-*")):
        if (path / "pyproject.toml").is_file():
            packages.append(DriverPackage(slug=path.name.removeprefix("jumpstarter-driver-"), path=path))
    return packages


def _is_test_file(path: Path) -> bool:
    return path.name.endswith("_test.py") or path.name == "conftest.py" or "tests" in path.parts


def _python_files(package: DriverPackage) -> list[Path]:
    return [path for path in package.path.rglob("*.py") if not _is_test_file(path)]


def _literal(node: ast.AST | None):
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def _driver_type_from_class(node: ast.ClassDef) -> set[str]:
    found: set[str] = set()
    for statement in node.body:
        if isinstance(statement, ast.Assign):
            targets = [t.id for t in statement.targets if isinstance(t, ast.Name)]
            if "driver_type" in targets:
                value = _literal(statement.value)
                if isinstance(value, str):
                    found.add(value)
        elif isinstance(statement, ast.AnnAssign):
            if isinstance(statement.target, ast.Name) and statement.target.id == "driver_type":
                value = _literal(statement.value)
                if isinstance(value, str):
                    found.add(value)
    return found


def _metrics_aliases(tree: ast.AST) -> set[str]:
    """Local names assigned from ``self.metrics`` (for example ``metrics = self.metrics``)."""
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "metrics"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
        ):
            aliases.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return aliases


def _metric_from_call(node: ast.Call, package: str, aliases: set[str] | None = None) -> DeclaredMetric | None:
    if not isinstance(node.func, ast.Attribute) or node.func.attr not in METRIC_TYPES:
        return None
    owner = node.func.value
    if isinstance(owner, ast.Attribute):
        if owner.attr != "metrics":
            return None
    elif isinstance(owner, ast.Name):
        if owner.id not in (aliases or set()):
            return None
    else:
        return None

    if len(node.args) < 2:
        return None
    short_name = _literal(node.args[0])
    help_text = _literal(node.args[1])
    if not isinstance(short_name, str) or not isinstance(help_text, str):
        return None

    labels, buckets = _metric_keywords(node)

    return DeclaredMetric(
        package=f"jumpstarter-driver-{package}",
        namespace=re.sub(r"[^a-z0-9_]", "_", package),
        metric_type=node.func.attr,
        short_name=short_name,
        help=help_text,
        labels=labels,
        buckets=buckets,
    )


def _metric_keywords(node: ast.Call) -> tuple[tuple[str, ...], tuple[float, ...] | None]:
    labels: tuple[str, ...] = ()
    buckets: tuple[float, ...] | None = None
    for keyword in node.keywords:
        if keyword.arg == "labels":
            value = _literal(keyword.value)
            if isinstance(value, (tuple, list)) and all(isinstance(item, str) for item in value):
                labels = tuple(value)
        elif keyword.arg == "buckets":
            value = _literal(keyword.value)
            if isinstance(value, (tuple, list)) and all(isinstance(item, (int, float)) for item in value):
                buckets = tuple(float(item) for item in value)
    return labels, buckets


def scan_package(package: DriverPackage) -> tuple[set[str], list[DeclaredMetric]]:
    """Return the declared driver types and literal metric declarations."""
    types: set[str] = set()
    metrics: list[DeclaredMetric] = []
    for path in _python_files(package):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue
        aliases = _metrics_aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                types |= _driver_type_from_class(node)
            elif isinstance(node, ast.Call):
                metric = _metric_from_call(node, package.slug, aliases)
                if metric is not None:
                    metrics.append(metric)
    return types, metrics


def read_baseline(repo: Path) -> set[str]:
    path = repo / _BASELINE_RELATIVE
    entries: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entries.add(line)
    return entries


def check_guard(packages: list[DriverPackage], baseline: set[str]) -> list[str]:
    errors: list[str] = []
    documented = set(DOCUMENTED_DRIVER_TYPES)
    for package in packages:
        types, _ = scan_package(package)
        if not types:
            if package.slug not in baseline:
                errors.append(f"{package.slug}: no driver_type and not listed in the baseline")
            continue
        if package.slug in baseline:
            errors.append(
                f"{package.slug}: stale baseline entry (declares {sorted(types)}); remove the line"
            )
        unknown = types - documented
        if unknown:
            errors.append(
                f"{package.slug}: undocumented driver_type {sorted(unknown)}; "
                f"documented set: {list(DOCUMENTED_DRIVER_TYPES)}"
            )
    return errors


def _definition_row(definition: MetricDefinition) -> dict:
    return {
        "name": definition.name,
        "short_name": definition.short_name,
        "namespace": definition.namespace,
        "type": definition.type,
        "help": definition.help,
        "labels": list(definition.labels),
        "buckets": list(definition.buckets) if definition.buckets is not None else None,
        "package": definition.package,
        "source": definition.source,
    }


def build_catalog(repo: Path, packages: list[DriverPackage]) -> list[dict]:
    from jumpstarter.metrics.registry import MetricsRegistry

    rows = [_definition_row(definition) for definition in MetricsRegistry().core_catalog()]
    for package in packages:
        _, metrics = scan_package(package)
        for metric in metrics:
            rows.append(
                {
                    "name": driver_metric_name(metric.namespace, metric.short_name, metric.metric_type),
                    "short_name": metric.short_name,
                    "namespace": metric.namespace,
                    "type": metric.metric_type,
                    "help": metric.help,
                    "labels": list(metric.labels),
                    "buckets": list(metric.buckets) if metric.buckets is not None else None,
                    "package": metric.package,
                    "source": f"{metric.package}:{metric.short_name}",
                }
            )
    return sorted(rows, key=lambda row: (row["package"], row["name"]))


def check_catalog(path: Path, rows: list[dict]) -> list[str]:
    if not path.is_file():
        return [f"{path}: missing; run UPDATE_CATALOG=1 make driver-telemetry-check"]
    existing = json.loads(path.read_text(encoding="utf-8"))
    old = {row["name"]: row for row in existing.get("metrics", [])}
    new = {row["name"]: row for row in rows}
    errors = [f"{path.name}: removed {name}" for name in sorted(set(old) - set(new))]
    errors += [f"{path.name}: added {name}" for name in sorted(set(new) - set(old))]
    errors += [
        f"{path.name}: changed {name}"
        for name in sorted(set(old) & set(new))
        if old[name] != new[name]
    ]
    return errors


def write_catalog(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_by": "python/scripts/check-driver-telemetry.py",
        "driver_type_allowlist": list(DOCUMENTED_DRIVER_TYPES),
        "metrics": rows,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _go_string_list(source: str, marker: str) -> set[str]:
    start = source.index(marker)
    brace = source.index("{", start)
    end = source.index("}", brace)
    return set(re.findall(r'"([^"]+)"', source[brace:end]))


def check_go_authorities(repo: Path) -> list[str]:
    telemetry = (repo / "controller/deploy/operator/internal/controller/jumpstarter/telemetry.go").read_text(
        encoding="utf-8"
    )
    merge = (repo / "controller/internal/service/metrics_merge.go").read_text(encoding="utf-8")
    types_source = (repo / "controller/deploy/operator/api/v1alpha1/jumpstarter_types.go").read_text(
        encoding="utf-8"
    )

    field_index = types_source.find("DriverTypeEnum []string")
    if field_index < 0:
        return ["jumpstarter_types.go: DriverTypeEnum field not found"]
    defaults = list(
        re.finditer(r"\+kubebuilder:default=\{([^}]*)\}", types_source[:field_index])
    )
    if not defaults:
        return ["jumpstarter_types.go: DriverTypeEnum kubebuilder default not found"]
    crd_default = defaults[-1]

    authorities = {
        "telemetry.go": _go_string_list(telemetry, "defaultTelemetryDriverTypeEnum = []string"),
        "metrics_merge.go": _go_string_list(merge, "DefaultDriverTypeEnum = []string"),
        "jumpstarter_types.go": set(re.findall(r'"([^"]+)"', crd_default.group(1))),
    }
    expected = set(DOCUMENTED_DRIVER_TYPES)
    errors = []
    for name, found in authorities.items():
        if found != expected:
            errors.append(
                f"{name}: allowlist {sorted(found)} does not match the documented set {sorted(expected)}"
            )
    return errors


def check_docs(repo: Path) -> list[str]:
    page = repo / _DOCS_RELATIVE
    if not page.is_file():
        return [f"{page}: missing"]
    text = page.read_text(encoding="utf-8")
    errors = [f"{page.name}: missing {needle!r}" for needle in _DOC_REQUIRED if needle not in text]
    errors += [
        f"{page.name}: missing category {category!r}"
        for category in DOCUMENTED_DRIVER_TYPES
        if category not in text
    ]
    return errors


def main(repo: Path | None = None, update: bool | None = None) -> int:
    repo = repo or repo_root()
    update = os.environ.get("UPDATE_CATALOG") == "1" if update is None else update

    packages = discover_packages(repo)
    baseline = read_baseline(repo)
    rows = build_catalog(repo, packages)

    errors: list[str] = []
    if not packages:
        errors.append("no jumpstarter-driver-* packages discovered")
    errors += check_guard(packages, baseline)
    errors += check_go_authorities(repo)
    errors += check_docs(repo)

    catalog_path = repo / _CATALOG_RELATIVE
    if update:
        write_catalog(catalog_path, rows)
        print(f"wrote {catalog_path.relative_to(repo)} ({len(rows)} rows)")
    else:
        errors += check_catalog(catalog_path, rows)

    if errors:
        print(f"driver telemetry check failed ({len(errors)} problem(s)):")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"driver telemetry check passed ({len(packages)} packages, {len(rows)} catalog rows)")
    return 0
