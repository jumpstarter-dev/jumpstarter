from __future__ import annotations

from pathlib import Path

import pytest
import yaml

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_config_yaml_validates_as_driver_instance():
    jmp_exporter = pytest.importorskip("jumpstarter.config.exporter")

    data = yaml.safe_load((EXAMPLES_DIR / "config.yaml").read_text())
    jmp_exporter.ExporterConfigV1Alpha1DriverInstance.model_validate(data)
