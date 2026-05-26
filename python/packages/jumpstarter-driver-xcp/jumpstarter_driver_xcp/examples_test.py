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


def test_config_can_yaml_validates_driver_instances():
    jmp_exporter = pytest.importorskip("jumpstarter.config.exporter")
    ExporterConfigV1Alpha1DriverInstance = jmp_exporter.ExporterConfigV1Alpha1DriverInstance

    data = yaml.safe_load((EXAMPLES_DIR / "config_can.yaml").read_text())
    for _name, driver_data in data["export"].items():
        ExporterConfigV1Alpha1DriverInstance.model_validate(driver_data)


def test_config_ethernet_udp_yaml_validates_driver_instances():
    jmp_exporter = pytest.importorskip("jumpstarter.config.exporter")
    ExporterConfigV1Alpha1DriverInstance = jmp_exporter.ExporterConfigV1Alpha1DriverInstance

    data = yaml.safe_load((EXAMPLES_DIR / "config_ethernet_udp.yaml").read_text())
    for _name, driver_data in data["export"].items():
        ExporterConfigV1Alpha1DriverInstance.model_validate(driver_data)


def test_config_using_a_pyxcp_config_file_yaml_validates_driver_instances():
    jmp_exporter = pytest.importorskip("jumpstarter.config.exporter")
    ExporterConfigV1Alpha1DriverInstance = jmp_exporter.ExporterConfigV1Alpha1DriverInstance

    data = yaml.safe_load((EXAMPLES_DIR / "config_using_a_pyxcp_config_file.yaml").read_text())
    for _name, driver_data in data["export"].items():
        ExporterConfigV1Alpha1DriverInstance.model_validate(driver_data)


def test_usage_py_compiles():
    source = (EXAMPLES_DIR / "usage.py").read_text()
    compile(source, "usage.py", "exec")
