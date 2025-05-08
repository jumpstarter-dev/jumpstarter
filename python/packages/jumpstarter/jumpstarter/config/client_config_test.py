import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError

from jumpstarter.common.exceptions import FileNotFoundError
from jumpstarter.config.client import ClientConfigV1Alpha1, ClientConfigV1Alpha1Drivers
from jumpstarter.config.common import ObjectMeta
from jumpstarter.config.env import JMP_DRIVERS_ALLOW, JMP_ENDPOINT, JMP_NAME, JMP_NAMESPACE, JMP_TOKEN


def test_client_ensure_exists_makes_dir(monkeypatch: pytest.MonkeyPatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(ClientConfigV1Alpha1, "CLIENT_CONFIGS_PATH", Path(d) / "clients")
        ClientConfigV1Alpha1.ensure_exists()
        assert os.path.exists(ClientConfigV1Alpha1.CLIENT_CONFIGS_PATH)


def test_client_config_try_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(JMP_NAMESPACE, "default")
    monkeypatch.setenv(JMP_NAME, "testclient")
    monkeypatch.setenv(JMP_TOKEN, "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz")
    monkeypatch.setenv(JMP_ENDPOINT, "jumpstarter.my-lab.com:1443")
    monkeypatch.setenv(JMP_DRIVERS_ALLOW, "jumpstarter.drivers.*,vendorpackage.*")

    config = ClientConfigV1Alpha1.try_from_env()
    assert config.alias == "default"
    assert config.metadata.namespace == "default"
    assert config.metadata.name == "testclient"
    assert config.token == "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"
    assert config.endpoint == "jumpstarter.my-lab.com:1443"
    assert config.drivers.allow == ["jumpstarter.drivers.*", "vendorpackage.*"]
    assert config.drivers.unsafe is False


def test_client_config_try_from_env_not_set():
    config = ClientConfigV1Alpha1.try_from_env()
    assert config is None


def test_client_config_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(JMP_NAMESPACE, "default")
    monkeypatch.setenv(JMP_NAME, "testclient")
    monkeypatch.setenv(JMP_TOKEN, "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz")
    monkeypatch.setenv(JMP_ENDPOINT, "jumpstarter.my-lab.com:1443")
    monkeypatch.setenv(JMP_DRIVERS_ALLOW, "jumpstarter.drivers.*,vendorpackage.*")

    config = ClientConfigV1Alpha1.from_env()
    assert config.alias == "default"
    assert config.metadata.namespace == "default"
    assert config.metadata.name == "testclient"
    assert config.token == "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"
    assert config.endpoint == "jumpstarter.my-lab.com:1443"
    assert config.drivers.allow == ["jumpstarter.drivers.*", "vendorpackage.*"]
    assert config.drivers.unsafe is False


def test_client_config_from_env_allow_unsafe(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(JMP_NAMESPACE, "default")
    monkeypatch.setenv(JMP_NAME, "testclient")
    monkeypatch.setenv(JMP_TOKEN, "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz")
    monkeypatch.setenv(JMP_ENDPOINT, "jumpstarter.my-lab.com:1443")
    monkeypatch.setenv(JMP_DRIVERS_ALLOW, "UNSAFE")

    config = ClientConfigV1Alpha1.from_env()
    assert config.alias == "default"
    assert config.metadata.namespace == "default"
    assert config.metadata.name == "testclient"
    assert config.token == "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"
    assert config.endpoint == "jumpstarter.my-lab.com:1443"
    assert config.drivers.unsafe is True


@pytest.mark.parametrize("missing_field", [JMP_NAMESPACE, JMP_NAME])
def test_client_config_from_env_missing_field_raises(monkeypatch: pytest.MonkeyPatch, missing_field):
    monkeypatch.setenv(JMP_NAMESPACE, "default")
    monkeypatch.setenv(JMP_NAME, "testclient")
    monkeypatch.setenv(JMP_TOKEN, "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz")
    monkeypatch.setenv(JMP_ENDPOINT, "jumpstarter.my-lab.com:1443")
    monkeypatch.setenv(JMP_DRIVERS_ALLOW, "jumpstarter.drivers.*,vendorpackage.*")

    monkeypatch.delenv(missing_field)

    with pytest.raises(ValidationError):
        _ = ClientConfigV1Alpha1.from_env()


def test_client_config_from_file():
    CLIENT_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: ClientConfig
metadata:
  namespace: default
  name: testclient
endpoint: jumpstarter.my-lab.com:1443
token: dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz
drivers:
  allow:
  - jumpstarter.drivers.*
  - vendorpackage.*
"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(CLIENT_CONFIG)
        f.close()
        config = ClientConfigV1Alpha1.from_file(f.name)
        assert config.alias == f.name.split("/")[-1]
        assert config.metadata.namespace == "default"
        assert config.metadata.name == "testclient"
        assert config.endpoint == "jumpstarter.my-lab.com:1443"
        assert config.token == "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"
        assert config.drivers.allow == ["jumpstarter.drivers.*", "vendorpackage.*"]
        assert config.drivers.unsafe is False
        os.unlink(f.name)


@pytest.mark.parametrize("invalid_field", ["apiVersion", "kind"])
def test_client_config_from_file_invalid_field_raises(invalid_field):
    CLIENT_CONFIG = {
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "ClientConfig",
        "endpoint": "jumpstarter.my-lab.com:1443",
        "token": "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz",
        "drivers": {"allow": ["jumpstarter.drivers.*", "vendorpackage.*"]},
    }

    CLIENT_CONFIG[invalid_field] = "foo"
    with tempfile.NamedTemporaryFile(mode="w") as f:
        yaml.safe_dump(CLIENT_CONFIG, f, sort_keys=False)
        with pytest.raises(ValueError):
            _ = ClientConfigV1Alpha1.from_file(f.name)


@pytest.mark.parametrize("missing_field", ["token", "endpoint", "drivers"])
def test_client_config_from_file_missing_field_raises(missing_field):
    CLIENT_CONFIG = {
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "ClientConfig",
        "endpoint": "jumpstarter.my-lab.com:1443",
        "token": "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz",
        "drivers": {"allow": ["jumpstarter.drivers.*", "vendorpackage.*"]},
    }

    del CLIENT_CONFIG[missing_field]
    with tempfile.NamedTemporaryFile(mode="w") as f:
        yaml.safe_dump(CLIENT_CONFIG, f, sort_keys=False)
        with pytest.raises(ValidationError):
            _ = ClientConfigV1Alpha1.from_file(f.name)


@pytest.mark.parametrize("invalid_field", ["allow"])
def test_client_config_from_file_invalid_drivers_field_raises(invalid_field):
    CLIENT_CONFIG = {
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "ClientConfig",
        "endpoint": "jumpstarter.my-lab.com:1443",
        "token": "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz",
        "drivers": {"allow": ["jumpstarter.drivers.*", "vendorpackage.*"]},
    }

    CLIENT_CONFIG["drivers"][invalid_field] = "foo"
    with tempfile.NamedTemporaryFile(mode="w") as f:
        yaml.safe_dump(CLIENT_CONFIG, f, sort_keys=False)
        with pytest.raises(ValidationError):
            _ = ClientConfigV1Alpha1.from_file(f.name)


def test_client_config_load():
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write("")
        f.close()
        with patch.object(ClientConfigV1Alpha1, "_get_path", return_value=Path(f.name)) as get_path_mock:
            with patch.object(
                ClientConfigV1Alpha1,
                "from_file",
                return_value=ClientConfigV1Alpha1(
                    alias="another",
                    metadata=ObjectMeta(namespace="default", name="another"),
                    endpoint="abc",
                    token="123",
                    drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=False),
                ),
            ) as from_file_mock:
                value = ClientConfigV1Alpha1.load("another")
                assert value.alias == "another"
                get_path_mock.assert_called_once_with("another")
                from_file_mock.assert_called_once_with(Path(f.name))
                os.unlink(f.name)


def test_client_config_load_not_found_raises():
    with pytest.raises(FileNotFoundError):
        _ = ClientConfigV1Alpha1.load("1235jklhbafsvd90u1234fsad")


def test_client_config_save(monkeypatch: pytest.MonkeyPatch):
    CLIENT_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: ClientConfig
metadata:
  namespace: default
  name: testclient
endpoint: jumpstarter.my-lab.com:1443
tls:
  ca: ''
  insecure: false
token: dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz
grpcOptions: {}
drivers:
  allow:
  - jumpstarter.drivers.*
  - vendorpackage.*
  unsafe: false
"""
    config = ClientConfigV1Alpha1(
        alias="testclient",
        metadata=ObjectMeta(namespace="default", name="testclient"),
        endpoint="jumpstarter.my-lab.com:1443",
        token="dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz",
        drivers=ClientConfigV1Alpha1Drivers(allow=["jumpstarter.drivers.*", "vendorpackage.*"], unsafe=False),
    )
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        with patch.object(ClientConfigV1Alpha1, "_get_path", return_value=Path(f.name)) as _get_path_mock:
            with patch.object(ClientConfigV1Alpha1, "ensure_exists"):
                ClientConfigV1Alpha1.save(config)
                with open(f.name) as loaded:
                    value = loaded.read()
                    assert value == CLIENT_CONFIG
        _get_path_mock.assert_called_once_with("testclient")
        os.unlink(f.name)


def test_client_config_save_explicit_path():
    CLIENT_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: ClientConfig
metadata:
  namespace: default
  name: testclient
endpoint: jumpstarter.my-lab.com:1443
tls:
  ca: ''
  insecure: false
token: dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz
grpcOptions: {}
drivers:
  allow:
  - jumpstarter.drivers.*
  - vendorpackage.*
  unsafe: false
"""
    config = ClientConfigV1Alpha1(
        alias="testclient",
        metadata=ObjectMeta(namespace="default", name="testclient"),
        endpoint="jumpstarter.my-lab.com:1443",
        token="dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz",
        drivers=ClientConfigV1Alpha1Drivers(allow=["jumpstarter.drivers.*", "vendorpackage.*"], unsafe=False),
    )
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        with patch.object(ClientConfigV1Alpha1, "ensure_exists"):
            ClientConfigV1Alpha1.save(config, f.name)
            with open(f.name) as loaded:
                value = loaded.read()
                assert value == CLIENT_CONFIG
        os.unlink(f.name)


def test_client_config_save_unsafe_drivers():
    CLIENT_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: ClientConfig
metadata:
  namespace: default
  name: testclient
endpoint: jumpstarter.my-lab.com:1443
tls:
  ca: ''
  insecure: false
token: dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz
grpcOptions: {}
drivers:
  allow: []
  unsafe: true
"""
    config = ClientConfigV1Alpha1(
        alias="testclient",
        metadata=ObjectMeta(namespace="default", name="testclient"),
        endpoint="jumpstarter.my-lab.com:1443",
        token="dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz",
        drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=True),
    )
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        with patch.object(ClientConfigV1Alpha1, "ensure_exists"):
            ClientConfigV1Alpha1.save(config, f.name)
            with open(f.name) as loaded:
                value = loaded.read()
                assert value == CLIENT_CONFIG
        os.unlink(f.name)


def test_client_config_exists():
    with patch.object(
        ClientConfigV1Alpha1, "_get_path", return_value=Path("/users/adsf/.config/jumpstarter/clients/abc.yaml")
    ) as _get_path_mock:
        assert ClientConfigV1Alpha1.exists("abc") is False
        _get_path_mock.assert_called_once_with("abc")


def test_client_config_list(monkeypatch: pytest.MonkeyPatch):
    CLIENT_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: ClientConfig
metadata:
  namespace: default
  name: testclient
endpoint: jumpstarter.my-lab.com:1443
token: dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz
drivers:
  allow:
  - jumpstarter.drivers.*
  - vendorpackage.*
"""
    with tempfile.TemporaryDirectory() as d:
        with open(Path(d) / "testclient.yaml", "w") as f:
            f.write(CLIENT_CONFIG)
            f.close()

        monkeypatch.setattr(ClientConfigV1Alpha1, "CLIENT_CONFIGS_PATH", Path(d))
        configs = ClientConfigV1Alpha1.list().items
        assert len(configs) == 1
        assert configs[0].alias == "testclient"


def test_client_config_list_none(monkeypatch: pytest.MonkeyPatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(ClientConfigV1Alpha1, "CLIENT_CONFIGS_PATH", Path(d))
        configs = ClientConfigV1Alpha1.list().items
        assert len(configs) == 0


def test_client_config_list_not_found_returns_empty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ClientConfigV1Alpha1, "CLIENT_CONFIGS_PATH", Path("/homeless-shelter"))
    configs = ClientConfigV1Alpha1.list().items
    assert len(configs) == 0


def test_client_config_delete():
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        with patch.object(ClientConfigV1Alpha1, "_get_path", return_value=Path(f.name)) as _get_path_mock:
            f.write("")
            f.close()
            ClientConfigV1Alpha1.delete("testclient")
            _get_path_mock.assert_called_once_with("testclient")
            assert os.path.exists(f.name) is False


def test_client_config_delete_does_not_exist_raises():
    with patch.object(
        ClientConfigV1Alpha1, "_get_path", return_value=Path("/asdf/2134/cv/clients/xyz.yaml")
    ) as _get_path_mock:
        with pytest.raises(FileNotFoundError):
            ClientConfigV1Alpha1.delete("xyz")
        _get_path_mock.assert_called_once_with("xyz")
