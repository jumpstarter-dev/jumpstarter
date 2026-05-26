from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest
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
    if kind and kind in KIND_TO_MODEL:
        model_class = _resolve_model(KIND_TO_MODEL[kind])
        model_class.model_validate(data)
        return

    for section_key, model_path in SECTION_TO_MODEL.items():
        if section_key in data:
            model_class = _resolve_model(model_path)
            section = data[section_key]
            if section_key == "export" and isinstance(section, dict):
                for _name, entry in section.items():
                    model_class.model_validate(entry)
            elif section_key == "hooks" and isinstance(section, dict):
                model_class.model_validate(section)
            return


def validate_python_example(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    compile(source, path.name, "exec")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            importlib.import_module(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                importlib.import_module(alias.name)


def discover_examples(examples_dir: Path) -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    for f in sorted(examples_dir.glob("**/*.yaml")):
        if f.name == "exporter.yaml":
            continue
        items.append((f, "yaml"))
    for f in sorted(examples_dir.glob("**/*.py")):
        items.append((f, "python"))
    return items


def make_example_test_params(examples_dir: Path) -> list[pytest.param]:
    params: list[pytest.param] = []
    for path, kind in discover_examples(examples_dir):
        params.append(pytest.param(path, kind, id=path.name))
    return params


def validate_example(path: Path, kind: str) -> None:
    if kind == "yaml":
        validate_yaml_example(path)
    elif kind == "python":
        validate_python_example(path)
