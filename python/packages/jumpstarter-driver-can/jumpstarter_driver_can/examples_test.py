from __future__ import annotations

from pathlib import Path
import yaml

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_config_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config.yaml").read_text())
    assert data is not None

def test_config_isotppython_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_isotppython.yaml").read_text())
    assert data is not None

def test_config_isotpsocket_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_isotpsocket.yaml").read_text())
    assert data is not None
