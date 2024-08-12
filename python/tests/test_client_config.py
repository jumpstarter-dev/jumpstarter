import os
import tempfile
from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError

from jumpstarter.config import (
    ClientConfigV1Alpha1,
    ClientConfigV1Alpha1Client,
    ClientConfigV1Alpha1Drivers,
)
from jumpstarter.config.env import JMP_DRIVERS_ALLOW, JMP_ENDPOINT, JMP_TOKEN


def test_client_ensure_exists_makes_dir(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(ClientConfigV1Alpha1, "CLIENT_CONFIGS_PATH", f"{d}/clients")
        ClientConfigV1Alpha1.ensure_exists()
        assert os.path.exists(ClientConfigV1Alpha1.CLIENT_CONFIGS_PATH)


def test_client_config_try_from_env(monkeypatch):
    monkeypatch.setenv(JMP_TOKEN, "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz")
    monkeypatch.setenv(JMP_ENDPOINT, "grpcs://jumpstarter.my-lab.com:1443")
    monkeypatch.setenv(JMP_DRIVERS_ALLOW, "jumpstarter.drivers.*,vendorpackage.*")

    config = ClientConfigV1Alpha1.try_from_env()
    assert config.name == "default"
    assert config.client.token == "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"
    assert config.client.endpoint == "grpcs://jumpstarter.my-lab.com:1443"
    assert config.client.drivers.allow == ["jumpstarter.drivers.*", "vendorpackage.*"]
    assert config.client.drivers.unsafe is False


def test_client_config_try_from_env_not_set():
    config = ClientConfigV1Alpha1.try_from_env()
    assert config is None


def test_client_config_from_env(monkeypatch):
    monkeypatch.setenv(JMP_TOKEN, "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz")
    monkeypatch.setenv(JMP_ENDPOINT, "grpcs://jumpstarter.my-lab.com:1443")
    monkeypatch.setenv(JMP_DRIVERS_ALLOW, "jumpstarter.drivers.*,vendorpackage.*")

    config = ClientConfigV1Alpha1.from_env()
    assert config.name == "default"
    assert config.client.token == "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"
    assert config.client.endpoint == "grpcs://jumpstarter.my-lab.com:1443"
    assert config.client.drivers.allow == ["jumpstarter.drivers.*", "vendorpackage.*"]
    assert config.client.drivers.unsafe is False


def test_client_config_from_env_allow_unsafe(monkeypatch):
    monkeypatch.setenv(JMP_TOKEN, "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz")
    monkeypatch.setenv(JMP_ENDPOINT, "grpcs://jumpstarter.my-lab.com:1443")
    monkeypatch.setenv(JMP_DRIVERS_ALLOW, "UNSAFE")

    config = ClientConfigV1Alpha1.from_env()
    assert config.name == "default"
    assert config.client.token == "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"
    assert config.client.endpoint == "grpcs://jumpstarter.my-lab.com:1443"
    assert config.client.drivers.allow == []
    assert config.client.drivers.unsafe is True


@pytest.mark.parametrize("missing_field", [JMP_TOKEN, JMP_ENDPOINT])
def test_client_config_from_env_missing_field_raises(monkeypatch, missing_field):
    monkeypatch.setenv(JMP_TOKEN, "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz")
    monkeypatch.setenv(JMP_ENDPOINT, "grpcs://jumpstarter.my-lab.com:1443")
    monkeypatch.setenv(JMP_DRIVERS_ALLOW, "jumpstarter.drivers.*,vendorpackage.*")

    monkeypatch.delenv(missing_field)

    with pytest.raises(ValidationError):
        _ = ClientConfigV1Alpha1.from_env()


def test_client_config_from_file():
    CLIENT_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: Client
client:
  endpoint: grpcs://jumpstarter.my-lab.com:1443
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
        assert config.name == f.name.split("/")[-1]
        assert config.client.endpoint == "grpcs://jumpstarter.my-lab.com:1443"
        assert (
            config.client.token == "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"
        )
        assert config.client.drivers.allow == ["jumpstarter.drivers.*", "vendorpackage.*"]
        assert config.client.drivers.unsafe is False
        os.unlink(f.name)


@pytest.mark.parametrize("invalid_field", ["apiVersion", "kind"])
def test_client_config_from_file_invalid_field_raises(invalid_field):
    CLIENT_CONFIG = {
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "Client",
        "client": {
            "endpoint": "grpcs://jumpstarter.my-lab.com:1443",
            "token": "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz",
            "drivers": {"allow": ["jumpstarter.drivers.*", "vendorpackage.*"]},
        },
    }

    CLIENT_CONFIG[invalid_field] = "foo"
    with tempfile.NamedTemporaryFile(mode="w") as f:
        yaml.safe_dump(CLIENT_CONFIG, f, sort_keys=False)
        with pytest.raises(ValueError):
            _ = ClientConfigV1Alpha1.from_file(f.name)


@pytest.mark.parametrize("missing_field", ["client"])
def test_client_config_from_file_missing_field_raises(missing_field):
    CLIENT_CONFIG = {
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "Client",
        "client": {
            "endpoint": "grpcs://jumpstarter.my-lab.com:1443",
            "token": "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz",
            "drivers": {"allow": ["jumpstarter.drivers.*", "vendorpackage.*"]},
        },
    }

    del CLIENT_CONFIG[missing_field]
    with tempfile.NamedTemporaryFile(mode="w") as f:
        yaml.safe_dump(CLIENT_CONFIG, f, sort_keys=False)
        with pytest.raises(ValidationError):
            _ = ClientConfigV1Alpha1.from_file(f.name)


@pytest.mark.parametrize("missing_field", ["token", "endpoint", "drivers"])
def test_client_config_from_file_missing_client_field_raises(missing_field):
    CLIENT_CONFIG = {
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "Client",
        "client": {
            "endpoint": "grpcs://jumpstarter.my-lab.com:1443",
            "token": "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz",
            "drivers": {"allow": ["jumpstarter.drivers.*", "vendorpackage.*"]},
        },
    }

    del CLIENT_CONFIG["client"][missing_field]
    with tempfile.NamedTemporaryFile(mode="w") as f:
        yaml.safe_dump(CLIENT_CONFIG, f, sort_keys=False)
        with pytest.raises(ValidationError):
            _ = ClientConfigV1Alpha1.from_file(f.name)


@pytest.mark.parametrize("invalid_field", ["allow"])
def test_client_config_from_file_invalid_client_drivers_field_raises(invalid_field):
    CLIENT_CONFIG = {
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "Client",
        "client": {
            "endpoint": "grpcs://jumpstarter.my-lab.com:1443",
            "token": "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz",
            "drivers": {"allow": ["jumpstarter.drivers.*", "vendorpackage.*"]},
        },
    }

    CLIENT_CONFIG["client"]["drivers"][invalid_field] = "foo"
    with tempfile.NamedTemporaryFile(mode="w") as f:
        yaml.safe_dump(CLIENT_CONFIG, f, sort_keys=False)
        with pytest.raises(ValidationError):
            _ = ClientConfigV1Alpha1.from_file(f.name)


def test_client_config_load():
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write("")
        f.close()
        with patch.object(ClientConfigV1Alpha1, "_get_path", return_value=f.name) as get_path_mock:
            with patch.object(
                ClientConfigV1Alpha1,
                "from_file",
                return_value=ClientConfigV1Alpha1(
                    name="another",
                    client=ClientConfigV1Alpha1Client(
                        endpoint="abc",
                        token="123",
                        drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=False),
                    ),
                ),
            ) as from_file_mock:
                value = ClientConfigV1Alpha1.load("another")
        assert value.name == "another"
        get_path_mock.assert_called_once_with("another")
        from_file_mock.assert_called_once_with(f.name)
        os.unlink(f.name)


def test_client_config_load_not_found_raises():
    with pytest.raises(FileNotFoundError):
        _ = ClientConfigV1Alpha1.load("1235jklhbafsvd90u1234fsad")


def test_client_config_save(monkeypatch):
    CLIENT_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: Client
client:
  endpoint: grpcs://jumpstarter.my-lab.com:1443
  token: dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz
  drivers:
    allow:
    - jumpstarter.drivers.*
    - vendorpackage.*
    unsafe: false
"""
    config = ClientConfigV1Alpha1(
        name="testclient",
        client=ClientConfigV1Alpha1Client(
            endpoint="grpcs://jumpstarter.my-lab.com:1443",
            token="dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz",
            drivers=ClientConfigV1Alpha1Drivers(allow=["jumpstarter.drivers.*", "vendorpackage.*"], unsafe=False),
        ),
    )
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        with patch.object(ClientConfigV1Alpha1, "_get_path", return_value=f.name) as _get_path_mock:
            with patch.object(ClientConfigV1Alpha1, "ensure_exists"):
                ClientConfigV1Alpha1.save(config)
                with open(f.name) as loaded:
                    value = loaded.read()
                    assert value == CLIENT_CONFIG
        _get_path_mock.assert_called_once_with("testclient")
        os.unlink(f.name)


def test_client_config_save_explicit_path():
    CLIENT_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: Client
client:
  endpoint: grpcs://jumpstarter.my-lab.com:1443
  token: dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz
  drivers:
    allow:
    - jumpstarter.drivers.*
    - vendorpackage.*
    unsafe: false
"""
    config = ClientConfigV1Alpha1(
        name="testclient",
        client=ClientConfigV1Alpha1Client(
            endpoint="grpcs://jumpstarter.my-lab.com:1443",
            token="dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz",
            drivers=ClientConfigV1Alpha1Drivers(allow=["jumpstarter.drivers.*", "vendorpackage.*"], unsafe=False),
        ),
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
kind: Client
client:
  endpoint: grpcs://jumpstarter.my-lab.com:1443
  token: dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz
  drivers:
    allow: []
    unsafe: true
"""
    config = ClientConfigV1Alpha1(
        name="testclient",
        client=ClientConfigV1Alpha1Client(
            endpoint="grpcs://jumpstarter.my-lab.com:1443",
            token="dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz",
            drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=True),
        ),
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
        ClientConfigV1Alpha1, "_get_path", return_value="/users/adsf/.config/jumpstarter/clients/abc.yaml"
    ) as _get_path_mock:
        assert ClientConfigV1Alpha1.exists("abc") is False
        _get_path_mock.assert_called_once_with("abc")


def test_client_config_list(monkeypatch):
    CLIENT_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: Client
client:
  endpoint: grpcs://jumpstarter.my-lab.com:1443
  token: dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz
  drivers:
    allow:
    - jumpstarter.drivers.*
    - vendorpackage.*
"""
    d = tempfile.TemporaryDirectory()
    with open(f"{d.name}/testclient.yaml", "w") as f:
        f.write(CLIENT_CONFIG)
        f.close()

    monkeypatch.setattr(ClientConfigV1Alpha1, "CLIENT_CONFIGS_PATH", d.name)
    configs = ClientConfigV1Alpha1.list()
    assert len(configs) == 1
    assert configs[0].name == "testclient"
    d.cleanup()


def test_client_config_list_none(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(ClientConfigV1Alpha1, "CLIENT_CONFIGS_PATH", d)
        configs = ClientConfigV1Alpha1.list()
        assert len(configs) == 0


def test_client_config_list_not_found_returns_empty(monkeypatch):
    monkeypatch.setattr(ClientConfigV1Alpha1, "CLIENT_CONFIGS_PATH", "/homeless-shelter")
    configs = ClientConfigV1Alpha1.list()
    assert len(configs) == 0


def test_client_config_delete():
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        with patch.object(ClientConfigV1Alpha1, "_get_path", return_value=f.name) as _get_path_mock:
            f.write("")
            f.close()
            ClientConfigV1Alpha1.delete("testclient")
            _get_path_mock.assert_called_once_with("testclient")
            assert os.path.exists(f.name) is False


def test_client_config_delete_does_not_exist_raises():
    with patch.object(
        ClientConfigV1Alpha1, "_get_path", return_value="/asdf/2134/cv/clients/xyz.yaml"
    ) as _get_path_mock:
        with pytest.raises(FileNotFoundError):
            ClientConfigV1Alpha1.delete("xyz")
        _get_path_mock.assert_called_once_with("xyz")
