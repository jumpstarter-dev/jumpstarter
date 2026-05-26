from __future__ import annotations

from pathlib import Path
import yaml

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_config_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config.yaml").read_text())
    assert data is not None

def test_config_digitalinput_configuration_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_digitalinput_configuration.yaml").read_text())
    assert data is not None

def test_config_powerswitch_configuration_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_powerswitch_configuration.yaml").read_text())
    assert data is not None
