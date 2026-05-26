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


def test_config_1_1_nat_yaml_validates_driver_instances():
    jmp_exporter = pytest.importorskip("jumpstarter.config.exporter")
    ExporterConfigV1Alpha1DriverInstance = jmp_exporter.ExporterConfigV1Alpha1DriverInstance

    data = yaml.safe_load((EXAMPLES_DIR / "config_1_1_nat.yaml").read_text())
    for _name, driver_data in data["export"].items():
        ExporterConfigV1Alpha1DriverInstance.model_validate(driver_data)


def test_config_custom_dns_entries_yaml_validates_driver_instances():
    jmp_exporter = pytest.importorskip("jumpstarter.config.exporter")
    ExporterConfigV1Alpha1DriverInstance = jmp_exporter.ExporterConfigV1Alpha1DriverInstance

    data = yaml.safe_load((EXAMPLES_DIR / "config_custom_dns_entries.yaml").read_text())
    for _name, driver_data in data["export"].items():
        ExporterConfigV1Alpha1DriverInstance.model_validate(driver_data)


def test_config_disabled_nat_dhcp_only_yaml_validates_driver_instances():
    jmp_exporter = pytest.importorskip("jumpstarter.config.exporter")
    ExporterConfigV1Alpha1DriverInstance = jmp_exporter.ExporterConfigV1Alpha1DriverInstance

    data = yaml.safe_load((EXAMPLES_DIR / "config_disabled_nat_dhcp_only.yaml").read_text())
    for _name, driver_data in data["export"].items():
        ExporterConfigV1Alpha1DriverInstance.model_validate(driver_data)


def test_exporter_1to1_nat_yaml_validates_as_exporter_config():
    jmp_exporter = pytest.importorskip("jumpstarter.config.exporter")
    ExporterConfigV1Alpha1 = jmp_exporter.ExporterConfigV1Alpha1

    data = yaml.safe_load((EXAMPLES_DIR / "exporter-1to1-nat.yaml").read_text())
    ExporterConfigV1Alpha1.model_validate(data)


def test_usage_py_compiles():
    source = (EXAMPLES_DIR / "usage.py").read_text()
    compile(source, "usage.py", "exec")
