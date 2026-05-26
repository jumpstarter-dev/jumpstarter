from __future__ import annotations

from pathlib import Path
import yaml

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_config_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config.yaml").read_text())
    assert data is not None

def test_config_exporterconfig_example_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_exporterconfig_example.yaml").read_text())
    assert data is not None

def test_config_exporterconfig_example_1_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_exporterconfig_example_1.yaml").read_text())
    assert data is not None
