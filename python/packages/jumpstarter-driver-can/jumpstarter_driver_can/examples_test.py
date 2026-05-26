from __future__ import annotations

from pathlib import Path

import yaml

from jumpstarter.config.exporter import ExporterConfigV1Alpha1DriverInstance

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_config_yaml_validates_driver_instances():
    data = yaml.safe_load((EXAMPLES_DIR / "config.yaml").read_text())
    for _name, driver_data in data["export"].items():
        ExporterConfigV1Alpha1DriverInstance.model_validate(driver_data)


def test_config_isotppython_yaml_validates_driver_instances():
    data = yaml.safe_load((EXAMPLES_DIR / "config_isotppython.yaml").read_text())
    for _name, driver_data in data["export"].items():
        ExporterConfigV1Alpha1DriverInstance.model_validate(driver_data)


def test_config_isotpsocket_yaml_validates_driver_instances():
    data = yaml.safe_load((EXAMPLES_DIR / "config_isotpsocket.yaml").read_text())
    for _name, driver_data in data["export"].items():
        ExporterConfigV1Alpha1DriverInstance.model_validate(driver_data)
