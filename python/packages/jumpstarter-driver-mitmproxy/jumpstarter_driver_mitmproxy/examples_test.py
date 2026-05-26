from __future__ import annotations

from pathlib import Path
import yaml

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_config_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config.yaml").read_text())
    assert data is not None

def test_config_mock_scenarios_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_mock_scenarios.yaml").read_text())
    assert data is not None


def test_usage_py_compiles():
    source = (EXAMPLES_DIR / "usage.py").read_text()
    compile(source, "usage.py", "exec")


def test_usage_exporter_side_path_py_compiles():
    source = (EXAMPLES_DIR / "usage_exporter_side_path.py").read_text()
    compile(source, "usage_exporter_side_path.py", "exec")


def test_usage_mock_scenarios_py_compiles():
    source = (EXAMPLES_DIR / "usage_mock_scenarios.py").read_text()
    compile(source, "usage_mock_scenarios.py", "exec")


def test_usage_basic_usage_py_compiles():
    source = (EXAMPLES_DIR / "usage_basic_usage.py").read_text()
    compile(source, "usage_basic_usage.py", "exec")


def test_usage_context_managers_py_compiles():
    source = (EXAMPLES_DIR / "usage_context_managers.py").read_text()
    compile(source, "usage_context_managers.py", "exec")


def test_usage_request_capture_py_compiles():
    source = (EXAMPLES_DIR / "usage_request_capture.py").read_text()
    compile(source, "usage_request_capture.py", "exec")


def test_usage_conditional_responses_py_compiles():
    source = (EXAMPLES_DIR / "usage_conditional_responses.py").read_text()
    compile(source, "usage_conditional_responses.py", "exec")


def test_usage_response_sequences_py_compiles():
    source = (EXAMPLES_DIR / "usage_response_sequences.py").read_text()
    compile(source, "usage_response_sequences.py", "exec")


def test_usage_dynamic_templates_py_compiles():
    source = (EXAMPLES_DIR / "usage_dynamic_templates.py").read_text()
    compile(source, "usage_dynamic_templates.py", "exec")


def test_usage_simulated_latency_py_compiles():
    source = (EXAMPLES_DIR / "usage_simulated_latency.py").read_text()
    compile(source, "usage_simulated_latency.py", "exec")


def test_usage_file_serving_py_compiles():
    source = (EXAMPLES_DIR / "usage_file_serving.py").read_text()
    compile(source, "usage_file_serving.py", "exec")


def test_usage_custom_addon_scripts_py_compiles():
    source = (EXAMPLES_DIR / "usage_custom_addon_scripts.py").read_text()
    compile(source, "usage_custom_addon_scripts.py", "exec")


def test_usage_state_store_py_compiles():
    source = (EXAMPLES_DIR / "usage_state_store.py").read_text()
    compile(source, "usage_state_store.py", "exec")
