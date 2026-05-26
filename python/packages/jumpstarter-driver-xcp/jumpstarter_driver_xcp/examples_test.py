from __future__ import annotations

from pathlib import Path
import yaml

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_config_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config.yaml").read_text())
    assert data is not None

def test_config_ethernet_udp_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_ethernet_udp.yaml").read_text())
    assert data is not None

def test_config_can_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_can.yaml").read_text())
    assert data is not None

def test_config_using_a_pyxcp_config_file_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_using_a_pyxcp_config_file.yaml").read_text())
    assert data is not None


def test_usage_py_compiles():
    source = (EXAMPLES_DIR / "usage.py").read_text()
    compile(source, "usage.py", "exec")
