import yaml


def test_exporters_config_is_valid_yaml(examples_root):
    config_file = examples_root / "introduction" / "exporter_config.yaml"
    data = yaml.safe_load(config_file.read_text())
    assert data is not None
    assert data["apiVersion"] == "jumpstarter.dev/v1alpha1"
    assert data["kind"] == "ExporterConfig"


def test_drivers_config_is_valid_yaml(examples_root):
    config_file = examples_root / "introduction" / "driver_exporter_config.yaml"
    data = yaml.safe_load(config_file.read_text())
    assert data is not None
    assert data["apiVersion"] == "jumpstarter.dev/v1alpha1"
    assert data["kind"] == "ExporterConfig"


def test_exporters_config_validates_against_model(examples_root):
    from jumpstarter.config.exporter import ExporterConfigV1Alpha1

    config_file = examples_root / "introduction" / "exporter_config.yaml"
    data = yaml.safe_load(config_file.read_text())
    config = ExporterConfigV1Alpha1.model_validate(data)
    assert config.metadata.name == "demo"
    assert "power" in config.export
    assert "serial" in config.export


def test_drivers_config_validates_against_model(examples_root):
    from jumpstarter.config.exporter import ExporterConfigV1Alpha1

    config_file = examples_root / "introduction" / "driver_exporter_config.yaml"
    data = yaml.safe_load(config_file.read_text())
    config = ExporterConfigV1Alpha1.model_validate(data)
    assert config.metadata.name == "demo"
    assert "power" in config.export
    assert "serial" in config.export


def test_exporters_config_has_expected_drivers(examples_root):
    config_file = examples_root / "introduction" / "exporter_config.yaml"
    data = yaml.safe_load(config_file.read_text())
    export = data["export"]
    assert "power" in export
    assert "serial" in export
    assert "storage" in export
    assert "custom" in export
    assert "reference" in export


def test_drivers_config_has_expected_drivers(examples_root):
    config_file = examples_root / "introduction" / "driver_exporter_config.yaml"
    data = yaml.safe_load(config_file.read_text())
    export = data["export"]
    assert "power" in export
    assert "serial" in export
    assert "qemu" in export
