import os
import platform
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TERM"] = "dumb"

try:
    from jumpstarter.common.utils import serve
    from jumpstarter.config.exporter import ExporterConfigV1Alpha1, ExporterConfigV1Alpha1DriverInstance
except ImportError:
    # some packages in the workspace does not depend on jumpstarter
    pass
else:

    @contextmanager
    def run(config):
        with serve(ExporterConfigV1Alpha1DriverInstance.from_str(config).instantiate()) as client:
            yield client

    @pytest.fixture(autouse=True)
    def jumpstarter_namespace(doctest_namespace):
        doctest_namespace["serve"] = serve
        doctest_namespace["run"] = run

    @pytest.fixture(autouse=True)
    def tmp_config_path(tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "client-config"))
        monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", tmp_path / "exporters")

    @pytest.fixture(autouse=True)
    def console_size(monkeypatch):
        monkeypatch.setenv("COLUMNS", "1024")
        monkeypatch.setenv("LINES", "1024")


# ---------------------------------------------------------------------------
# Allure integration: auto-label tests by package and component
# ---------------------------------------------------------------------------
_PACKAGE_SUITES = {
    "jumpstarter": "Core",
    "jumpstarter-protocol": "Core",
    "jumpstarter-testing": "Testing",
    "jumpstarter-kubernetes": "Kubernetes",
    "jumpstarter-imagehash": "Utilities",
}

try:
    import allure

    def _extract_package_name(item):
        path_str = str(item.path)
        if "/packages/" in path_str:
            return path_str.split("/packages/")[1].split("/")[0]
        return None

    def _classify_package(pkg_name):
        if pkg_name in _PACKAGE_SUITES:
            return _PACKAGE_SUITES[pkg_name]
        if pkg_name.startswith("jumpstarter-driver-"):
            return "Drivers"
        if pkg_name.startswith("jumpstarter-cli"):
            return "CLI"
        return "Other"

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(items):
        for item in items:
            pkg = _extract_package_name(item)
            if pkg:
                item.add_marker(allure.parent_suite(_classify_package(pkg)))
                item.add_marker(allure.suite(pkg))

    def pytest_sessionstart(session):
        alluredir = session.config.getoption("alluredir", default=None)
        if alluredir:
            results_dir = Path(alluredir)
            results_dir.mkdir(parents=True, exist_ok=True)
            env_file = results_dir / "environment.properties"
            props = {
                "Python": sys.version.split()[0],
                "Platform": platform.platform(),
                "OS": platform.system(),
                "Architecture": platform.machine(),
            }
            with open(env_file, "w") as f:
                for key, value in props.items():
                    f.write(f"{key}={value}\n")

except ImportError:
    pass
