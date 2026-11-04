from pathlib import Path

import pytest

from . import configs
from .configs import is_path_like, load_client_config, load_exporter_config
from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter.config.env import JMP_CLIENT_CONFIG
from jumpstarter.config.exporter import ExporterConfigV1Alpha1


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


@pytest.fixture
def no_user_config(monkeypatch):
    from jumpstarter.config.user import UserConfigV1Alpha1

    def missing():
        raise FileNotFoundError("no user config")

    monkeypatch.setattr(UserConfigV1Alpha1, "load", staticmethod(missing))


@pytest.mark.parametrize(
    "value, expected",
    [
        ("lab", False),
        ("lab-eu", False),
        ("default", False),
        ("configs/ci.yaml", True),
        ("ci.yaml", True),
        ("ci.yml", True),
        ("./client", True),
        ("~/client", True),
        ("/abs/client", True),
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

    def test_relative_path_without_base_dir(self, recorded):
        load_client_config("ci.yaml")
        assert recorded == [("file", Path("ci.yaml"))]

    def test_absolute_path_ignores_base_dir(self, recorded, tmp_path):
        load_client_config(str(tmp_path / "ci.yaml"), base_dir=Path("/elsewhere"))
        assert recorded == [("file", tmp_path / "ci.yaml")]

    def test_home_is_expanded(self, recorded, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        load_client_config("~/ci.yaml", base_dir=Path("/elsewhere"))
        assert recorded == [("file", tmp_path / "ci.yaml")]

    def test_env(self, recorded):
        assert load_client_config("env") == "env"

    def test_explicit_value_beats_environment(self, recorded, monkeypatch):
        monkeypatch.setenv(JMP_CLIENT_CONFIG, "/from/env.yaml")
        assert load_client_config("lab") == "alias:lab"

    def test_environment_variable(self, recorded, monkeypatch, no_user_config):
        monkeypatch.setenv(JMP_CLIENT_CONFIG, "/from/env.yaml")
        load_client_config(None)
        assert recorded == [("file", Path("/from/env.yaml"))]

    def test_current_client(self, recorded, monkeypatch):
        from jumpstarter.config.user import UserConfigV1Alpha1

        class User:
            class config:
                current_client = "the-current-client"

        monkeypatch.setattr(UserConfigV1Alpha1, "load", staticmethod(lambda: User()))
        assert load_client_config() == "the-current-client"
        assert recorded == []

    def test_default_alias_last(self, recorded, no_user_config):
        assert load_client_config("") == "alias:default"

    def test_no_current_client_falls_back_to_default(self, recorded, monkeypatch):
        from jumpstarter.config.user import UserConfigV1Alpha1

        class User:
            class config:
                current_client = None

        monkeypatch.setattr(UserConfigV1Alpha1, "load", staticmethod(lambda: User()))
        assert load_client_config() == "alias:default"

    def test_real_file(self, tmp_path):
        (tmp_path / "client.yaml").write_text(
            "apiVersion: jumpstarter.dev/v1alpha1\n"
            "kind: ClientConfig\n"
            "metadata:\n  namespace: default\n  name: ci\n"
            "endpoint: grpc.example.com:443\n"
            "token: ''\n"
            "drivers:\n  allow: []\n"
        )
        config = load_client_config("client.yaml", base_dir=tmp_path)
        assert isinstance(config, ClientConfigV1Alpha1)
        assert config.endpoint == "grpc.example.com:443"


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


def test_env_constant():
    assert configs.ENV_CONFIG == "env"
