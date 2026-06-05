from __future__ import annotations

import warnings

import pytest
import yaml as _yaml
from pydantic import ValidationError

from jumpstarter.config.exporter import (
    ExporterConfigV1Alpha1,
    ExporterConfigV1Alpha1DriverInstance,
    HookConfigV1Alpha1,
    HookInstanceConfigV1Alpha1,
)
from jumpstarter.testing.examples import (
    instantiate_yaml_example,
    validate_example,
    validate_python_example,
    validate_yaml_example,
)


def _dump_exporter_config(**overrides) -> str:
    config = ExporterConfigV1Alpha1(
        metadata={"name": "test", "namespace": "default"},
        endpoint="grpc.example.com:443",
        token="xxxxx",
        **overrides,
    )
    return _yaml.dump(config.model_dump(by_alias=True, exclude_none=True))


def test_validate_yaml_example_with_exporter_config(tmp_path):
    driver = ExporterConfigV1Alpha1DriverInstance.model_validate(
        {"type": "jumpstarter_driver_shell.driver.Shell", "config": {"methods": {"ls": "ls"}}}
    )
    f = tmp_path / "config.yaml"
    f.write_text(_dump_exporter_config(export={"shell": driver}))
    validate_yaml_example(f)


def test_validate_yaml_example_with_export_section(tmp_path):
    driver = ExporterConfigV1Alpha1DriverInstance.model_validate(
        {"type": "jumpstarter_driver_shell.driver.Shell", "config": {"methods": {"ls": "ls"}}}
    )
    f = tmp_path / "export.yaml"
    f.write_text(_yaml.dump({"export": {"shell": driver.model_dump(by_alias=True, exclude_none=True)}}))
    validate_yaml_example(f)


def test_validate_yaml_example_warns_on_unknown_structure(tmp_path):
    f = tmp_path / "fragment.yaml"
    f.write_text(_yaml.dump({"device": "/dev/ttyUSB0"}))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        validate_yaml_example(f)
    assert any("no model validation" in str(w.message).lower() for w in caught)


def test_validate_yaml_example_asserts_on_empty(tmp_path):
    f = tmp_path / "empty.yaml"
    f.write_text("")
    with pytest.raises(AssertionError, match="parsed as empty"):
        validate_yaml_example(f)


def test_validate_yaml_example_accepts_non_dict(tmp_path):
    f = tmp_path / "list.yaml"
    f.write_text(_yaml.dump(["item1", "item2"]))
    validate_yaml_example(f)


def test_validate_python_example_valid_syntax(tmp_path):
    f = tmp_path / "usage.py"
    f.write_text("x = 1 + 2\n")
    validate_python_example(f)


def test_validate_python_example_raises_on_syntax_error(tmp_path):
    f = tmp_path / "broken.py"
    f.write_text("def foo(\n")
    with pytest.raises(SyntaxError):
        validate_python_example(f)


def test_validate_python_example_skips_on_missing_import(tmp_path):
    f = tmp_path / "usage.py"
    f.write_text("from nonexistent_package_xyz import something\n")
    with pytest.raises(pytest.skip.Exception):
        validate_python_example(f)


def test_validate_python_example_skips_on_missing_plain_import(tmp_path):
    f = tmp_path / "usage.py"
    f.write_text("import nonexistent_package_xyz\n")
    with pytest.raises(pytest.skip.Exception):
        validate_python_example(f)


def test_validate_example_dispatches_yaml(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text(_yaml.dump(["item"]))
    validate_example(f, "yaml")


def test_validate_example_dispatches_python(tmp_path):
    f = tmp_path / "usage.py"
    f.write_text("x = 1\n")
    validate_example(f, "python")


def test_validate_yaml_example_with_hooks_section(tmp_path):
    hook = HookConfigV1Alpha1(
        before_lease=HookInstanceConfigV1Alpha1(script="echo hello"),
    )
    f = tmp_path / "hooks.yaml"
    f.write_text(_yaml.dump({"hooks": hook.model_dump(by_alias=True, exclude_none=True)}))
    validate_yaml_example(f)


def test_instantiate_yaml_example_skips_without_export(tmp_path):
    hook = HookConfigV1Alpha1(
        before_lease=HookInstanceConfigV1Alpha1(script="echo hi"),
    )
    f = tmp_path / "hooks.yaml"
    f.write_text(_yaml.dump({"hooks": hook.model_dump(by_alias=True, exclude_none=True)}))
    with pytest.raises(pytest.skip.Exception, match="no export section"):
        instantiate_yaml_example(f)


def test_validate_yaml_example_rejects_invalid_exporter_config(tmp_path):
    f = tmp_path / "invalid_exporter.yaml"
    f.write_text(
        _yaml.dump({"apiVersion": "jumpstarter.dev/v1alpha1", "kind": "ExporterConfig"})
    )
    with pytest.raises(ValidationError):
        validate_yaml_example(f)


def test_validate_yaml_example_raises_on_unrecognized_kind(tmp_path):
    f = tmp_path / "bad_kind.yaml"
    f.write_text(_yaml.dump({"apiVersion": "jumpstarter.dev/v1alpha1", "kind": "ExporerConfig"}))
    with pytest.raises(ValueError, match="unrecognized kind"):
        validate_yaml_example(f)


def test_validate_yaml_example_rejects_invalid_export_entry(tmp_path):
    f = tmp_path / "invalid_export.yaml"
    f.write_text(_yaml.dump({"export": {"dev": "not-a-dict"}}))
    with pytest.raises(ValidationError):
        validate_yaml_example(f)


def test_validate_yaml_example_validates_all_matching_sections(tmp_path):
    hook = HookConfigV1Alpha1(
        before_lease=HookInstanceConfigV1Alpha1(script="echo hello"),
    )
    f = tmp_path / "combined.yaml"
    f.write_text(
        _yaml.dump(
            {
                "hooks": hook.model_dump(by_alias=True, exclude_none=True),
                "export": {"dev": "not-a-dict"},
            }
        )
    )
    with pytest.raises(ValidationError):
        validate_yaml_example(f)


def test_validate_yaml_example_rejects_non_dict_hooks(tmp_path):
    f = tmp_path / "bad_hooks.yaml"
    f.write_text(_yaml.dump({"hooks": ["not", "a", "dict"]}))
    with pytest.raises(TypeError, match="hooks"):
        validate_yaml_example(f)


def test_validate_example_dispatches_bash(tmp_path):
    f = tmp_path / "usage_cli.bash"
    f.write_text("#!/bin/bash\necho hello\n")
    validate_example(f, "bash")


def test_validate_yaml_example_rejects_non_dict_export(tmp_path):
    f = tmp_path / "bad_export.yaml"
    f.write_text(_yaml.dump({"export": ["item1", "item2"]}))
    with pytest.raises(TypeError, match="export"):
        validate_yaml_example(f)


def test_validate_example_raises_on_unsupported_kind(tmp_path):
    f = tmp_path / "script.sh"
    f.write_text("echo hello\n")
    with pytest.raises(ValueError, match="unsupported"):
        validate_example(f, "unknown")


def test_instantiate_yaml_example_skips_on_missing_driver(tmp_path):
    driver = ExporterConfigV1Alpha1DriverInstance.model_validate(
        {"type": "nonexistent_driver_package.driver.Fake"}
    )
    f = tmp_path / "config.yaml"
    f.write_text(_yaml.dump({"export": {"dev": driver.model_dump(by_alias=True, exclude_none=True)}}))
    with pytest.raises(pytest.skip.Exception, match="driver 'dev'"):
        instantiate_yaml_example(f)
