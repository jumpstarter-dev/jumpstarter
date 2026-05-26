from __future__ import annotations

from pathlib import Path
import yaml

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_config_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config.yaml").read_text())
    assert data is not None

def test_config_tcp_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_tcp.yaml").read_text())
    assert data is not None

def test_config_static_remote_endpoint_no_service_discov_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_static_remote_endpoint_no_service_discov.yaml").read_text())
    assert data is not None


def test_usage_py_compiles():
    source = (EXAMPLES_DIR / "usage.py").read_text()
    compile(source, "usage.py", "exec")


def test_usage_service_discovery_rpc_py_compiles():
    source = (EXAMPLES_DIR / "usage_service_discovery_rpc.py").read_text()
    compile(source, "usage_service_discovery_rpc.py", "exec")


def test_usage_event_subscription_py_compiles():
    source = (EXAMPLES_DIR / "usage_event_subscription.py").read_text()
    compile(source, "usage_event_subscription.py", "exec")


def test_usage_raw_messaging_py_compiles():
    source = (EXAMPLES_DIR / "usage_raw_messaging.py").read_text()
    compile(source, "usage_raw_messaging.py", "exec")


def test_usage_connection_management_py_compiles():
    source = (EXAMPLES_DIR / "usage_connection_management.py").read_text()
    compile(source, "usage_connection_management.py", "exec")
