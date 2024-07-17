import os
import tempfile
from unittest.mock import patch

import pytest

from jumpstarter.client.config import Config


def setup_function():
    for key in [
        "JUMPSTARTER_ENDPOINT",
        "JUMPSTARTER_TOKEN",
        "JUMPSTARTER_CONFIG",
        "JUMPSTARTER_CONTEXT",
    ]:
        if key in os.environ:
            del os.environ[key]


def test_config_file_path_from_env():
    os.environ["JUMPSTARTER_CONFIG"] = "/tmp/config.yaml"
    os.environ["JUMPSTARTER_CONTEXT"] = "dummy_context"
    with patch.object(Config, "_load", return_value=None) as _:
        config = Config()
        assert config._filename == "/tmp/config.yaml"


def test_config_file_path_without_context():
    os.environ["JUMPSTARTER_CONFIG"] = ""
    os.environ["JUMPSTARTER_CONTEXT"] = ""
    with patch.object(Config, "_load", return_value=None) as _:
        config = Config()
        assert config._filename.endswith("/.config/jumpstarter/config.yaml")


def test_config_file_path_with_context():
    os.environ["JUMPSTARTER_CONFIG"] = ""
    os.environ["JUMPSTARTER_CONTEXT"] = "mycontext"
    with patch.object(Config, "_load", return_value=None) as _:
        config = Config()
        assert config._filename.endswith("/.config/jumpstarter/config_mycontext.yaml")


def test_config_file_path_with_argument_context():
    os.environ["JUMPSTARTER_CONFIG"] = ""
    os.environ["JUMPSTARTER_CONTEXT"] = "mycontext"
    with patch.object(Config, "_load", return_value=None) as _:
        config = Config(context="othercontext")
        assert config._filename.endswith(
            "/.config/jumpstarter/config_othercontext.yaml"
        )


def test_config_from_env():
    os.environ["JUMPSTARTER_ENDPOINT"] = "localhost:8080"
    os.environ["JUMPSTARTER_TOKEN"] = "mytoken"
    config = Config()
    assert config.endpoint == "localhost:8080"
    assert config.token == "mytoken"
    assert config.name == "client"


def test_config_from_file():
    CONFIG = """
client:
    endpoint: yaml-host:8080
    token: yaml-token
    name: yaml-client
"""

    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(CONFIG)
        f.close()
        os.environ["JUMPSTARTER_CONFIG"] = f.name
        config = Config()
        assert config.endpoint == "yaml-host:8080"
        assert config.token == "yaml-token"
        assert config.name == "yaml-client"
        os.unlink(f.name)


def test_config_from_file_with_missing_token():
    CONFIG = """
client:
    endpoint: yaml-host:8080
    name: yaml-client
"""

    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(CONFIG)
        f.close()
        os.environ["JUMPSTARTER_CONFIG"] = f.name
        with pytest.raises(ValueError):
            Config()
        os.unlink(f.name)


def test_config_from_file_with_missing_endpoint():
    CONFIG = """
client:
    token: abcd
    name: yaml-client
"""

    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(CONFIG)
        f.close()
        os.environ["JUMPSTARTER_CONFIG"] = f.name
        with pytest.raises(ValueError):
            Config()
        os.unlink(f.name)


def test_config_from_file_with_missing_name():
    CONFIG = """
client:
    endpoint: yaml-host:8080
    token: yaml-token
"""

    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(CONFIG)
        f.close()
        os.environ["JUMPSTARTER_CONFIG"] = f.name
        config = Config()
        assert config.endpoint == "yaml-host:8080"
        assert config.token == "yaml-token"
        assert config.name == "client"
        os.unlink(f.name)
