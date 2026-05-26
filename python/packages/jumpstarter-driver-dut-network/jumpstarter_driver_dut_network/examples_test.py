from __future__ import annotations

from pathlib import Path
import yaml

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_config_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config.yaml").read_text())
    assert data is not None

def test_config_1_1_nat_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_1_1_nat.yaml").read_text())
    assert data is not None

def test_config_disabled_nat_dhcp_only_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_disabled_nat_dhcp_only.yaml").read_text())
    assert data is not None

def test_config_custom_dns_entries_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_custom_dns_entries.yaml").read_text())
    assert data is not None


def test_usage_py_compiles():
    source = (EXAMPLES_DIR / "usage.py").read_text()
    compile(source, "usage.py", "exec")
