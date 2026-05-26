from __future__ import annotations

from pathlib import Path

import yaml

from jumpstarter.config.exporter import ExporterConfigV1Alpha1DriverInstance

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_config_yaml_validates_driver_instances():
    data = yaml.safe_load((EXAMPLES_DIR / "config.yaml").read_text())
    for _name, driver_data in data["export"].items():
        ExporterConfigV1Alpha1DriverInstance.model_validate(driver_data)


def test_config_gpio_based_bootsel_entry_yaml_validates_driver_instances():
    data = yaml.safe_load((EXAMPLES_DIR / "config_gpio_based_bootsel_entry.yaml").read_text())
    for _name, driver_data in data["export"].items():
        ExporterConfigV1Alpha1DriverInstance.model_validate(driver_data)


def test_pico_exporter_gpio_yaml_validates_as_exporter_config():
    from jumpstarter.config.exporter import ExporterConfigV1Alpha1

    data = yaml.safe_load((EXAMPLES_DIR / "pico-exporter-gpio.yaml").read_text())
    ExporterConfigV1Alpha1.model_validate(data)


def test_pico_exporter_standalone_yaml_validates_as_exporter_config():
    from jumpstarter.config.exporter import ExporterConfigV1Alpha1

    data = yaml.safe_load((EXAMPLES_DIR / "pico-exporter-standalone.yaml").read_text())
    ExporterConfigV1Alpha1.model_validate(data)


def test_pico_exporter_yaml_validates_as_exporter_config():
    from jumpstarter.config.exporter import ExporterConfigV1Alpha1

    data = yaml.safe_load((EXAMPLES_DIR / "pico-exporter.yaml").read_text())
    ExporterConfigV1Alpha1.model_validate(data)
