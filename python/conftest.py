import os
from contextlib import contextmanager

import pytest
from hypothesis import HealthCheck, settings
from hypothesis.database import DirectoryBasedExampleDatabase

os.environ["TERM"] = "dumb"

_original_start_listening = DirectoryBasedExampleDatabase._start_listening


def _robust_start_listening(self):
    try:
        _original_start_listening(self)
    except (FileNotFoundError, OSError):
        self._observer = None


DirectoryBasedExampleDatabase._start_listening = _robust_start_listening

_original_stop_listening = DirectoryBasedExampleDatabase._stop_listening


def _robust_stop_listening(self):
    if self._observer is None:
        return
    _original_stop_listening(self)


DirectoryBasedExampleDatabase._stop_listening = _robust_stop_listening

settings.register_profile("ci", max_examples=100)
settings.register_profile(
    "fuzz",
    max_examples=500,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "ci"))

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
