import os
import tempfile
from unittest.mock import patch

import pytest

from jumpstarter.config.client import ClientConfigV1Alpha1, ClientConfigV1Alpha1Drivers, ObjectMeta
from jumpstarter.config.user import UserConfigV1Alpha1, UserConfigV1Alpha1Config


def test_user_config_exists(monkeypatch: pytest.MonkeyPatch):
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write("")
        f.close()
        monkeypatch.setattr(UserConfigV1Alpha1, "USER_CONFIG_PATH", f.name)
        assert UserConfigV1Alpha1.exists() is True
        os.unlink(f.name)


def test_user_config_load(monkeypatch: pytest.MonkeyPatch):
    USER_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: UserConfig
config:
  current-client: testclient
"""
    with patch.object(
        ClientConfigV1Alpha1,
        "load",
        return_value=ClientConfigV1Alpha1(
            alias="testclient",
            metadata=ObjectMeta(namespace="default", name="testclient"),
            endpoint="abc",
            token="123",
            drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=False),
        ),
    ) as mock_load:
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write(USER_CONFIG)
            f.close()
            monkeypatch.setattr(UserConfigV1Alpha1, "USER_CONFIG_PATH", f.name)
            config = UserConfigV1Alpha1.load()
            mock_load.assert_called_once_with("testclient")
            assert config.config.current_client.alias == "testclient"
            os.unlink(f.name)


def test_user_config_load_does_not_exist(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(UserConfigV1Alpha1, "USER_CONFIG_PATH", "/nowhere/config.yaml`")
    with pytest.raises(FileNotFoundError):
        _ = UserConfigV1Alpha1.load()


def test_user_config_load_no_current_client(monkeypatch: pytest.MonkeyPatch):
    USER_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: UserConfig
config: {}
"""
    with patch.object(
        ClientConfigV1Alpha1,
        "load",
        return_value=ClientConfigV1Alpha1(
            alias="testclient",
            metadata=ObjectMeta(namespace="default", name="testclient"),
            endpoint="abc",
            token="123",
            drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=False),
        ),
    ) as mock_load:
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write(USER_CONFIG)
            f.close()
            monkeypatch.setattr(UserConfigV1Alpha1, "USER_CONFIG_PATH", f.name)
            config = UserConfigV1Alpha1.load()
            mock_load.assert_not_called()
            assert config.config.current_client is None
            os.unlink(f.name)


def test_user_config_load_current_client_empty(monkeypatch: pytest.MonkeyPatch):
    USER_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: UserConfig
config:
    current-client: null
"""
    with patch.object(
        ClientConfigV1Alpha1,
        "load",
        return_value=ClientConfigV1Alpha1(
            alias="testclient",
            metadata=ObjectMeta(namespace="default", name="testclient"),
            endpoint="abc",
            token="123",
            drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=False),
        ),
    ) as mock_load:
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write(USER_CONFIG)
            f.close()
            monkeypatch.setattr(UserConfigV1Alpha1, "USER_CONFIG_PATH", f.name)
            config = UserConfigV1Alpha1.load()
            mock_load.assert_not_called()
            assert config.config.current_client is None
            os.unlink(f.name)


def test_user_config_load_invalid_api_version_raises(monkeypatch: pytest.MonkeyPatch):
    USER_CONFIG = """apiVersion: abc
kind: UserConfig
config:
  current-client: null
"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(USER_CONFIG)
        f.close()
        monkeypatch.setattr(UserConfigV1Alpha1, "USER_CONFIG_PATH", f.name)
        with pytest.raises(ValueError):
            _ = UserConfigV1Alpha1.load()
        os.unlink(f.name)


def test_user_config_load_invalid_kind_raises(monkeypatch: pytest.MonkeyPatch):
    USER_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: ClientConfig
config:
  current-client: null
"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(USER_CONFIG)
        f.close()
        monkeypatch.setattr(UserConfigV1Alpha1, "USER_CONFIG_PATH", f.name)
        with pytest.raises(ValueError):
            _ = UserConfigV1Alpha1.load()
        os.unlink(f.name)


def test_user_config_load_no_config_raises(monkeypatch: pytest.MonkeyPatch):
    USER_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: UserConfig
"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(USER_CONFIG)
        f.close()
        monkeypatch.setattr(UserConfigV1Alpha1, "USER_CONFIG_PATH", f.name)
        with pytest.raises(ValueError):
            _ = UserConfigV1Alpha1.load()
        os.unlink(f.name)


def test_user_config_load_or_create_config_exists():
    with patch.object(UserConfigV1Alpha1, "exists", return_value=True) as mock_exists:
        with patch.object(
            UserConfigV1Alpha1,
            "load",
            return_value=UserConfigV1Alpha1(config=UserConfigV1Alpha1Config(current_client=None)),
        ) as mock_load:
            _ = UserConfigV1Alpha1.load_or_create()
            mock_exists.assert_called_once()
            mock_load.assert_called_once()


def test_user_config_load_or_create_dir_exists():
    with patch.object(UserConfigV1Alpha1, "exists", return_value=False) as mock_exists:
        with patch.object(os.path, "exists", return_value=True):
            with patch.object(UserConfigV1Alpha1, "save") as mock_save:
                _ = UserConfigV1Alpha1.load_or_create()
                mock_exists.assert_called_once()
                mock_save.assert_called_once_with(
                    UserConfigV1Alpha1(config=UserConfigV1Alpha1Config(current_client=None))
                )


def test_user_config_load_or_create_dir_does_not_exist():
    with tempfile.TemporaryDirectory() as d:
        UserConfigV1Alpha1.BASE_CONFIG_PATH = f"{d}/jumpstarter"
        UserConfigV1Alpha1.USER_CONFIG_PATH = f"{d}/jumpstarter/config.yaml"
        with patch.object(UserConfigV1Alpha1, "save") as mock_save:
            _ = UserConfigV1Alpha1.load_or_create()
            mock_save.assert_called_once_with(UserConfigV1Alpha1(config=UserConfigV1Alpha1Config(current_client=None)))


def test_user_config_save(monkeypatch: pytest.MonkeyPatch):
    USER_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: UserConfig
config:
  current-client: testclient
"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        monkeypatch.setattr(UserConfigV1Alpha1, "USER_CONFIG_PATH", f.name)
        config = UserConfigV1Alpha1(
            config=UserConfigV1Alpha1Config(
                current_client=ClientConfigV1Alpha1(
                    alias="testclient",
                    metadata=ObjectMeta(namespace="default", name="testclient"),
                    endpoint="abc",
                    token="123",
                    drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=False),
                )
            )
        )
        UserConfigV1Alpha1.save(config)
        with open(f.name) as loaded:
            value = loaded.read()
            assert value == USER_CONFIG
        os.unlink(f.name)


def test_user_config_save_no_current_client(monkeypatch: pytest.MonkeyPatch):
    USER_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: UserConfig
config:
  current-client: null
"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        monkeypatch.setattr(UserConfigV1Alpha1, "USER_CONFIG_PATH", f.name)
        config = UserConfigV1Alpha1(config=UserConfigV1Alpha1Config(current_client=None))
        UserConfigV1Alpha1.save(config)
        with open(f.name) as loaded:
            value = loaded.read()
            assert value == USER_CONFIG
        os.unlink(f.name)


def test_user_config_use_client(monkeypatch: pytest.MonkeyPatch):
    USER_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: UserConfig
config:
  current-client: testclient
"""
    with patch.object(
        ClientConfigV1Alpha1,
        "load",
        return_value=ClientConfigV1Alpha1(
            alias="testclient",
            metadata=ObjectMeta(namespace="default", name="testclient"),
            endpoint="abc",
            token="123",
            drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=False),
        ),
    ) as mock_load:
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            monkeypatch.setattr(UserConfigV1Alpha1, "USER_CONFIG_PATH", f.name)
            config = UserConfigV1Alpha1(
                config=UserConfigV1Alpha1Config(
                    current_client=ClientConfigV1Alpha1(
                        alias="another",
                        metadata=ObjectMeta(namespace="default", name="testclient"),
                        endpoint="abc",
                        token="123",
                        drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=False),
                    )
                )
            )
            config.use_client("testclient")
            with open(f.name) as loaded:
                value = loaded.read()
                assert value == USER_CONFIG
                mock_load.assert_called_once_with("testclient")
            assert config.config.current_client.alias == "testclient"
            os.unlink(f.name)


def test_user_config_use_client_none(monkeypatch: pytest.MonkeyPatch):
    USER_CONFIG = """apiVersion: jumpstarter.dev/v1alpha1
kind: UserConfig
config:
  current-client: null
"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        monkeypatch.setattr(UserConfigV1Alpha1, "USER_CONFIG_PATH", f.name)
        config = UserConfigV1Alpha1(
            config=UserConfigV1Alpha1Config(
                current_client=ClientConfigV1Alpha1(
                    alias="another",
                    metadata=ObjectMeta(namespace="default", name="testclient"),
                    endpoint="abc",
                    token="123",
                    drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=False),
                )
            )
        )
        config.use_client(None)
        with open(f.name) as loaded:
            value = loaded.read()
            assert value == USER_CONFIG
        assert config.config.current_client is None
        os.unlink(f.name)
