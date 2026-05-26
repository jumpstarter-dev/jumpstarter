from __future__ import annotations

from pathlib import Path
import yaml

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_config_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config.yaml").read_text())
    assert data is not None

def test_config_nxp_s32k388_opensomeip_zephyr_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_nxp_s32k388_opensomeip_zephyr.yaml").read_text())
    assert data is not None

def test_config_nucleo_h753zi_openbsw_zephyr_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_nucleo_h753zi_openbsw_zephyr.yaml").read_text())
    assert data is not None


def test_usage_py_compiles():
    source = (EXAMPLES_DIR / "usage.py").read_text()
    compile(source, "usage.py", "exec")
