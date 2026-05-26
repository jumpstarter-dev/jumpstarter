import yaml


def test_hooks_exporter_config_validates_against_model(examples_root):
    from jumpstarter.config.exporter import ExporterConfigV1Alpha1

    config_file = examples_root / "introduction" / "hooks_exporter_config.yaml"
    data = yaml.safe_load(config_file.read_text())
    config = ExporterConfigV1Alpha1.model_validate(data)
    assert config.hooks.before_lease is not None
    assert config.hooks.after_lease is not None
    assert config.hooks.before_lease.timeout == 60
    assert config.hooks.after_lease.on_failure == "warn"


def test_hook_device_init_is_valid_yaml(examples_root):
    from jumpstarter.config.exporter import HookConfigV1Alpha1

    config_file = examples_root / "introduction" / "hook_device_init.yaml"
    data = yaml.safe_load(config_file.read_text())
    config = HookConfigV1Alpha1.model_validate(data["hooks"])
    assert config.before_lease is not None
    assert config.before_lease.timeout == 120
    assert config.before_lease.on_failure == "endLease"


def test_hook_device_cleanup_is_valid_yaml(examples_root):
    from jumpstarter.config.exporter import HookConfigV1Alpha1

    config_file = examples_root / "introduction" / "hook_device_cleanup.yaml"
    data = yaml.safe_load(config_file.read_text())
    config = HookConfigV1Alpha1.model_validate(data["hooks"])
    assert config.after_lease is not None
    assert config.after_lease.timeout == 30
    assert config.after_lease.on_failure == "warn"


def test_hook_firmware_flash_is_valid_yaml(examples_root):
    from jumpstarter.config.exporter import HookConfigV1Alpha1

    config_file = examples_root / "introduction" / "hook_firmware_flash.yaml"
    data = yaml.safe_load(config_file.read_text())
    config = HookConfigV1Alpha1.model_validate(data["hooks"])
    assert config.before_lease is not None
    assert config.before_lease.timeout == 180


def test_hook_bash_is_valid_yaml(examples_root):
    from jumpstarter.config.exporter import HookConfigV1Alpha1

    config_file = examples_root / "introduction" / "hook_bash.yaml"
    data = yaml.safe_load(config_file.read_text())
    config = HookConfigV1Alpha1.model_validate(data["hooks"])
    assert config.before_lease is not None
    assert config.before_lease.exec_ == "/bin/bash"


def test_hook_python_config_is_valid_yaml(examples_root):
    from jumpstarter.config.exporter import HookConfigV1Alpha1

    config_file = examples_root / "introduction" / "hook_python_config.yaml"
    data = yaml.safe_load(config_file.read_text())
    config = HookConfigV1Alpha1.model_validate(data["hooks"])
    assert config.before_lease is not None
    assert config.before_lease.script == "/opt/jumpstarter/hooks/prepare_device.py"


def test_hook_python_example_is_valid_python(examples_root):
    example_file = examples_root / "introduction" / "hook_prepare_device.py"
    source = example_file.read_text()
    compile(source, str(example_file), "exec")


def test_hook_script_file_config_is_valid_yaml(examples_root):
    from jumpstarter.config.exporter import HookConfigV1Alpha1

    config_file = examples_root / "introduction" / "hook_script_file.yaml"
    data = yaml.safe_load(config_file.read_text())
    config = HookConfigV1Alpha1.model_validate(data["hooks"])
    assert config.before_lease is not None
    assert config.before_lease.exec_ == "/bin/bash"
    assert config.before_lease.script == "/opt/jumpstarter/hooks/prepare_device.sh"
