import os
from contextlib import contextmanager

import pytest

os.environ["TQDM_DISABLE"] = "1"

try:
    from jumpstarter.common.utils import serve
    from jumpstarter.config import ClientConfigV1Alpha1, ExporterConfigV1Alpha1DriverInstance, UserConfigV1Alpha1
    from jumpstarter.config.exporter import ExporterConfigV1Alpha1
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
        monkeypatch.setattr(UserConfigV1Alpha1, "USER_CONFIG_PATH", tmp_path / "config.yaml")
        monkeypatch.setattr(ClientConfigV1Alpha1, "CLIENT_CONFIGS_PATH", tmp_path / "clients")
        monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", tmp_path / "exporters")
