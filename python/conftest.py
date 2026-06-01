import os
from contextlib import contextmanager

import pytest

os.environ["TERM"] = "dumb"

try:
    from hypothesis import settings as hypothesis_settings

    hypothesis_settings.register_profile("ci", max_examples=100)
    hypothesis_settings.register_profile(
        "fuzz",
        max_examples=500,
    )
    hypothesis_settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "ci"))
except ImportError:
    pass

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
