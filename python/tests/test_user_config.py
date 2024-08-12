import os
import tempfile
from unittest.mock import patch

import pytest

from jumpstarter.config import ClientConfigV1Alpha1, ClientConfigV1Alpha1Client, ClientConfigV1Alpha1Drivers, UserConfig


def test_user_config_exists(monkeypatch):
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write("")
        f.close()
        monkeypatch.setattr(UserConfig, "USER_CONFIG_PATH", f.name)
        assert UserConfig.exists() is True
        os.unlink(f.name)


def test_user_config_load(monkeypatch):
    USER_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: UserConfig
config:
  current-client: testclient
"""
    with patch.object(
        ClientConfigV1Alpha1,
        "load",
        return_value=ClientConfigV1Alpha1(
            name="testclient",
            client=ClientConfigV1Alpha1Client(
                endpoint="abc", token="123", drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=False)
            ),
        ),
    ) as mock_load:
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write(USER_CONFIG)
            f.close()
            monkeypatch.setattr(UserConfig, "USER_CONFIG_PATH", f.name)
            config = UserConfig.load()
            mock_load.assert_called_once_with("testclient")
            assert config.current_client.name == "testclient"
            os.unlink(f.name)


def test_user_config_load_does_not_exist(monkeypatch):
    monkeypatch.setattr(UserConfig, "USER_CONFIG_PATH", "/nowhere/config.yaml`")
    with pytest.raises(FileNotFoundError):
        _ = UserConfig.load()


def test_user_config_load_no_current_client(monkeypatch):
    USER_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: UserConfig
config: {}
"""
    with patch.object(
        ClientConfigV1Alpha1,
        "load",
        return_value=ClientConfigV1Alpha1(
            name="testclient",
            client=ClientConfigV1Alpha1Client(
                endpoint="abc", token="123", drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=False)
            ),
        ),
    ) as mock_load:
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write(USER_CONFIG)
            f.close()
            monkeypatch.setattr(UserConfig, "USER_CONFIG_PATH", f.name)
            config = UserConfig.load()
            mock_load.assert_not_called()
            assert config.current_client is None
            os.unlink(f.name)


def test_user_config_load_current_client_empty(monkeypatch):
    USER_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: UserConfig
config:
    current-client: ""
"""
    with patch.object(
        ClientConfigV1Alpha1,
        "load",
        return_value=ClientConfigV1Alpha1(
            name="testclient",
            client=ClientConfigV1Alpha1Client(
                endpoint="abc", token="123", drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=False)
            ),
        ),
    ) as mock_load:
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write(USER_CONFIG)
            f.close()
            monkeypatch.setattr(UserConfig, "USER_CONFIG_PATH", f.name)
            config = UserConfig.load()
            mock_load.assert_not_called()
            assert config.current_client is None
            os.unlink(f.name)


def test_user_config_load_invalid_api_version_raises(monkeypatch):
    USER_CONFIG = """apiVersion: abc
kind: UserConfig
config:
  current-client: testclient
"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(USER_CONFIG)
        f.close()
        monkeypatch.setattr(UserConfig, "USER_CONFIG_PATH", f.name)
        with pytest.raises(ValueError):
            _ = UserConfig.load()
        os.unlink(f.name)


def test_user_config_load_invalid_kind_raises(monkeypatch):
    USER_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: ClientConfig
config:
  current-client: testclient
"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(USER_CONFIG)
        f.close()
        monkeypatch.setattr(UserConfig, "USER_CONFIG_PATH", f.name)
        with pytest.raises(ValueError):
            _ = UserConfig.load()
        os.unlink(f.name)


def test_user_config_load_no_config_raises(monkeypatch):
    USER_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: UserConfig
"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(USER_CONFIG)
        f.close()
        monkeypatch.setattr(UserConfig, "USER_CONFIG_PATH", f.name)
        with pytest.raises(ValueError):
            _ = UserConfig.load()
        os.unlink(f.name)


def test_user_config_load_or_create_config_exists():
    with patch.object(UserConfig, "exists", return_value=True) as mock_exists:
        with patch.object(UserConfig, "load", return_value=UserConfig(current_client=None)) as mock_load:
            _ = UserConfig.load_or_create()
            mock_exists.assert_called_once()
            mock_load.assert_called_once()


def test_user_config_load_or_create_dir_exists():
    with patch.object(UserConfig, "exists", return_value=False) as mock_exists:
        with patch.object(os.path, "exists", return_value=True):
            with patch.object(UserConfig, "save") as mock_save:
                _ = UserConfig.load_or_create()
                mock_exists.assert_called_once()
                mock_save.assert_called_once_with(UserConfig(current_client=None))


def test_user_config_load_or_create_dir_does_not_exist():
    with tempfile.TemporaryDirectory() as d:
        UserConfig.BASE_CONFIG_PATH = f"{d}/jumpstarter"
        UserConfig.USER_CONFIG_PATH = f"{d}/jumpstarter/config.yaml"
        with patch.object(UserConfig, "save") as mock_save:
            _ = UserConfig.load_or_create()
            mock_save.assert_called_once_with(UserConfig(current_client=None))


def test_user_config_save(monkeypatch):
    USER_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: UserConfig
config:
  current-client: testclient
"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        monkeypatch.setattr(UserConfig, "USER_CONFIG_PATH", f.name)
        config = UserConfig(
            current_client=ClientConfigV1Alpha1(
                name="testclient",
                client=ClientConfigV1Alpha1Client(
                    endpoint="abc", token="123", drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=False)
                ),
            )
        )
        UserConfig.save(config)
        with open(f.name) as loaded:
            value = loaded.read()
            assert value == USER_CONFIG
        os.unlink(f.name)


def test_user_config_save_no_current_client(monkeypatch):
    USER_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: UserConfig
config:
  current-client: ''
"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        monkeypatch.setattr(UserConfig, "USER_CONFIG_PATH", f.name)
        config = UserConfig(current_client=None)
        UserConfig.save(config)
        with open(f.name) as loaded:
            value = loaded.read()
            assert value == USER_CONFIG
        os.unlink(f.name)


def test_user_config_use_client(monkeypatch):
    USER_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: UserConfig
config:
  current-client: testclient
"""
    with patch.object(
        ClientConfigV1Alpha1,
        "load",
        return_value=ClientConfigV1Alpha1(
            name="testclient",
            client=ClientConfigV1Alpha1Client(
                endpoint="abc", token="123", drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=False)
            ),
        ),
    ) as mock_load:
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            monkeypatch.setattr(UserConfig, "USER_CONFIG_PATH", f.name)
            config = UserConfig(
                current_client=ClientConfigV1Alpha1(
                    name="another",
                    client=ClientConfigV1Alpha1Client(
                        endpoint="abc", token="123", drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=False)
                    ),
                )
            )
            config.use_client("testclient")
            with open(f.name) as loaded:
                value = loaded.read()
                assert value == USER_CONFIG
                mock_load.assert_called_once_with("testclient")
            assert config.current_client.name == "testclient"
            os.unlink(f.name)


def test_user_config_use_client_none(monkeypatch):
    USER_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: UserConfig
config:
  current-client: ''
"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        monkeypatch.setattr(UserConfig, "USER_CONFIG_PATH", f.name)
        config = UserConfig(
            current_client=ClientConfigV1Alpha1(
                name="another",
                client=ClientConfigV1Alpha1Client(
                    endpoint="abc", token="123", drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=False)
                ),
            )
        )
        config.use_client(None)
        with open(f.name) as loaded:
            value = loaded.read()
            assert value == USER_CONFIG
        assert config.current_client is None
        os.unlink(f.name)
