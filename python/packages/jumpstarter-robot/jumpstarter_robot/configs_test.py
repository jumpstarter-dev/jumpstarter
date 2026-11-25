
from types import SimpleNamespace

import pytest

from .configs import is_path_like, load_client_config, load_exporter_config
from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter.config.exporter import ExporterConfigV1Alpha1
from jumpstarter.config.user import UserConfigV1Alpha1


@pytest.fixture
def recorded(monkeypatch):
    """Replace every way of loading a client config with one that records how it was asked."""
    calls = []

    def recorder(kind):
        def load(value=None):
            calls.append((kind, value))
            return f"{kind}:{value}"

        return load

    monkeypatch.setattr(ClientConfigV1Alpha1, "load", staticmethod(recorder("alias")))
    monkeypatch.setattr(ClientConfigV1Alpha1, "from_file", staticmethod(recorder("file")))
    monkeypatch.setattr(ClientConfigV1Alpha1, "from_env", staticmethod(lambda: calls.append(("env", None)) or "env"))
    return calls


@pytest.mark.parametrize(
    "value, expected",
    [
        ("lab", False),
        ("configs/ci.yaml", True),
        ("ci.yaml", True),
        ("./client", True),
    ],
)
def test_is_path_like(value, expected):
    assert is_path_like(value) is expected


class TestClientConfig:
    def test_alias(self, recorded):
        assert load_client_config("lab-eu") == "alias:lab-eu"

    def test_relative_path_resolves_against_base_dir(self, recorded, tmp_path):
        load_client_config("configs/ci.yaml", base_dir=tmp_path)
        assert recorded == [("file", tmp_path / "configs" / "ci.yaml")]


    def test_env(self, recorded):
        assert load_client_config("env") == "env"

    def test_default_is_the_environment_then_the_current_client(self, monkeypatch):
        current = SimpleNamespace(config=SimpleNamespace(current_client="current"))
        monkeypatch.setattr(UserConfigV1Alpha1, "load_or_create", staticmethod(lambda: current))
        monkeypatch.setattr(ClientConfigV1Alpha1, "try_from_env", staticmethod(lambda: None))
        assert load_client_config() == "current"
        monkeypatch.setattr(ClientConfigV1Alpha1, "try_from_env", staticmethod(lambda: "env"))
        assert load_client_config() == "env"


EXPORTER_YAML = """\
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: mock
endpoint: ""
token: ""
export:
  power:
    type: jumpstarter_driver_power.driver.MockPower
"""


class TestExporterConfig:
    def test_path_relative_to_base_dir(self, tmp_path):
        (tmp_path / "exporter.yaml").write_text(EXPORTER_YAML)
        config = load_exporter_config("exporter.yaml", base_dir=tmp_path)
        assert isinstance(config, ExporterConfigV1Alpha1)
        assert config.metadata.name == "mock"

    def test_alias(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ExporterConfigV1Alpha1, "load", staticmethod(lambda alias: calls.append(alias) or "x"))
        assert load_exporter_config("mock") == "x"
        assert calls == ["mock"]

    def test_missing_path(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_exporter_config(str(tmp_path / "nope.yaml"))
