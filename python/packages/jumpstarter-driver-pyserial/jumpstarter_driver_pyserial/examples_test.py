from __future__ import annotations

from pathlib import Path

import pytest
import yaml


EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_config_yaml_validates_driver_instances():
    jmp_exporter = pytest.importorskip("jumpstarter.config.exporter")
    ExporterConfigV1Alpha1DriverInstance = jmp_exporter.ExporterConfigV1Alpha1DriverInstance

    data = yaml.safe_load((EXAMPLES_DIR / "config.yaml").read_text())
    for _name, driver_data in data["export"].items():
        ExporterConfigV1Alpha1DriverInstance.model_validate(driver_data)


def test_config_configuration_yaml_validates_driver_instances():
    jmp_exporter = pytest.importorskip("jumpstarter.config.exporter")
    ExporterConfigV1Alpha1DriverInstance = jmp_exporter.ExporterConfigV1Alpha1DriverInstance

    data = yaml.safe_load((EXAMPLES_DIR / "config_configuration.yaml").read_text())
    for _name, driver_data in data["export"].items():
        ExporterConfigV1Alpha1DriverInstance.model_validate(driver_data)


def test_config_device_auto_detection_yaml_is_valid_yaml():
    data = yaml.safe_load((EXAMPLES_DIR / "config_device_auto_detection.yaml").read_text())
    assert data is not None


def test_config_multiple_channels_example_yaml_validates_driver_instances():
    jmp_exporter = pytest.importorskip("jumpstarter.config.exporter")
    ExporterConfigV1Alpha1DriverInstance = jmp_exporter.ExporterConfigV1Alpha1DriverInstance

    data = yaml.safe_load((EXAMPLES_DIR / "config_multiple_channels_example.yaml").read_text())
    for _name, driver_data in data["export"].items():
        ExporterConfigV1Alpha1DriverInstance.model_validate(driver_data)


def test_config_single_channel_example_yaml_validates_driver_instances():
    jmp_exporter = pytest.importorskip("jumpstarter.config.exporter")
    ExporterConfigV1Alpha1DriverInstance = jmp_exporter.ExporterConfigV1Alpha1DriverInstance

    data = yaml.safe_load((EXAMPLES_DIR / "config_single_channel_example.yaml").read_text())
    for _name, driver_data in data["export"].items():
        ExporterConfigV1Alpha1DriverInstance.model_validate(driver_data)


def test_usage_py_compiles():
    source = (EXAMPLES_DIR / "usage.py").read_text()
    compile(source, "usage.py", "exec")


def test_usage_examples_py_compiles():
    source = (EXAMPLES_DIR / "usage_examples.py").read_text()
    compile(source, "usage_examples.py", "exec")


def test_usage_examples_1_py_compiles():
    source = (EXAMPLES_DIR / "usage_examples_1.py").read_text()
    compile(source, "usage_examples_1.py", "exec")


def test_usage_examples_2_py_compiles():
    source = (EXAMPLES_DIR / "usage_examples_2.py").read_text()
    compile(source, "usage_examples_2.py", "exec")
