from __future__ import annotations

from pathlib import Path
import yaml

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_config_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config.yaml").read_text())
    assert data is not None

def test_config_noyitopowerhid_4_8_channel_hid_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_noyitopowerhid_4_8_channel_hid.yaml").read_text())
    assert data is not None
