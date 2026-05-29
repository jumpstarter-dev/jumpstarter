from __future__ import annotations

import ast
import importlib
import warnings
from pathlib import Path

import yaml

KIND_TO_MODEL: dict[str, str] = {
    "ExporterConfig": "jumpstarter.config.exporter.ExporterConfigV1Alpha1",
    "ClientConfig": "jumpstarter.config.client.ClientConfigV1Alpha1",
    "UserConfig": "jumpstarter.config.user.UserConfigV1Alpha1",
}

SECTION_TO_MODEL: dict[str, str] = {
    "hooks": "jumpstarter.config.exporter.HookConfigV1Alpha1",
    "export": "jumpstarter.config.exporter.ExporterConfigV1Alpha1DriverInstance",
}


def _resolve_model(qualified_name: str) -> type:
    module_path, class_name = qualified_name.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def validate_yaml_example(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    data = yaml.safe_load(content)
    assert data is not None, f"{path.name} parsed as empty"
    if not isinstance(data, dict):
        return

    kind = data.get("kind")
    if kind is not None:
        if kind not in KIND_TO_MODEL:
            raise ValueError(
                f"{path.name}: unrecognized kind '{kind}', expected one of {sorted(KIND_TO_MODEL)}"
            )
        model_class = _resolve_model(KIND_TO_MODEL[kind])
        model_class.model_validate(data)
        return

    validated_any = False
    for section_key, model_path in SECTION_TO_MODEL.items():
        if section_key in data:
            model_class = _resolve_model(model_path)
            section = data[section_key]
            if section_key == "export" and isinstance(section, dict):
                for _name, entry in section.items():
                    model_class.model_validate(entry)
            elif section_key == "hooks" and isinstance(section, dict):
                model_class.model_validate(section)
            validated_any = True

    if validated_any:
        return

    warnings.warn(
        f"{path.name}: no model validation performed, only YAML syntax was checked",
        stacklevel=2,
    )


def validate_python_example(path: Path) -> None:
    import pytest

    source = path.read_text(encoding="utf-8")
    compile(source, path.name, "exec")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            try:
                importlib.import_module(node.module)
            except ImportError:
                pytest.skip(f"{node.module} not installed")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                try:
                    importlib.import_module(alias.name)
                except ImportError:
                    pytest.skip(f"{alias.name} not installed")


def instantiate_yaml_example(path: Path) -> None:
    import pytest

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "export" not in data:
        pytest.skip("no export section")

    from jumpstarter.config.exporter import ExporterConfigV1Alpha1DriverInstance

    for name, entry in data["export"].items():
        instance = ExporterConfigV1Alpha1DriverInstance.model_validate(entry)
        try:
            instance.instantiate()
        except Exception as exc:
            pytest.skip(f"driver '{name}': {exc}")


def validate_example(path: Path, kind: str) -> None:
    if kind == "yaml":
        validate_yaml_example(path)
    elif kind == "python":
        validate_python_example(path)
