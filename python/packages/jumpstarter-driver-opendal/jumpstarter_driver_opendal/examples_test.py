from __future__ import annotations

from pathlib import Path

import pytest
import yaml

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_config_yaml_validates_as_driver_instance():
    jmp_exporter = pytest.importorskip("jumpstarter.config.exporter")

    data = yaml.safe_load((EXAMPLES_DIR / "config.yaml").read_text())
    jmp_exporter.ExporterConfigV1Alpha1DriverInstance.model_validate(data)


def test_usage_py_compiles():
    source = (EXAMPLES_DIR / "usage.py").read_text()
    compile(source, "usage.py", "exec")


def test_usage_api_py_compiles():
    source = (EXAMPLES_DIR / "usage_api.py").read_text()
    compile(source, "usage_api.py", "exec")


def test_usage_examples_py_compiles():
    source = (EXAMPLES_DIR / "usage_examples.py").read_text()
    compile(source, "usage_examples.py", "exec")
