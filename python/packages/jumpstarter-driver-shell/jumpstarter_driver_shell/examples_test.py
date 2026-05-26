from __future__ import annotations

from pathlib import Path
import yaml

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_config_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config.yaml").read_text())
    assert data is not None

def test_config_format_2_unified_format_with_description_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_format_2_unified_format_with_description.yaml").read_text())
    assert data is not None

def test_config_cli_help_output_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_cli_help_output.yaml").read_text())
    assert data is not None
