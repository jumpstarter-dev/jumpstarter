from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from jumpstarter.testing.checks import (
    _is_referenced,
    discover_example_files,
    find_inline_code_blocks,
    find_unused_examples,
)

PACKAGES_DIR = Path(__file__).resolve().parent.parent / "packages"


def _discover_driver_packages() -> list[Path]:
    return sorted(
        pkg
        for pkg in PACKAGES_DIR.iterdir()
        if pkg.is_dir() and pkg.name.startswith("jumpstarter-driver-")
    )


def _example_file_params() -> list[pytest.param]:
    params = []
    for pkg in _discover_driver_packages():
        examples_dir = pkg / "examples"
        for path, kind in discover_example_files(examples_dir):
            rel = path.relative_to(pkg)
            params.append(pytest.param(path, kind, id=f"{pkg.name}/{rel}"))
    return params


def _driver_params() -> list[pytest.param]:
    return [
        pytest.param(pkg, id=pkg.name)
        for pkg in _discover_driver_packages()
        if (pkg / "examples").is_dir()
    ]


@pytest.mark.parametrize("path,kind", _example_file_params())
def test_example_validates(path, kind):
    testing = pytest.importorskip("jumpstarter.testing.examples")
    testing.validate_example(path, kind)


def test_is_referenced_rejects_bare_filename_in_prose(tmp_path):
    examples_dir = tmp_path / "examples"
    examples_dir.mkdir()
    example = examples_dir / "config.yaml"
    example.write_text("kind: ExporterConfig\n", encoding="utf-8")
    readme_content = "The config.yaml word appears in prose but not as a literalinclude path"
    assert not _is_referenced(example, examples_dir, readme_content)


def test_is_referenced_matches_relative_path(tmp_path):
    examples_dir = tmp_path / "examples"
    examples_dir.mkdir()
    example = examples_dir / "config.yaml"
    example.write_text("kind: ExporterConfig\n", encoding="utf-8")
    readme_content = "```{literalinclude} examples/config.yaml\n```"
    assert _is_referenced(example, examples_dir, readme_content)


def test_validate_yaml_warns_on_unrecognized_structure(tmp_path):
    yaml_file = tmp_path / "fragment.yaml"
    yaml_file.write_text("device: /dev/ttyUSB0\n", encoding="utf-8")
    testing = pytest.importorskip("jumpstarter.testing.examples")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        testing.validate_yaml_example(yaml_file)
    assert any("no model validation" in str(w.message).lower() for w in caught), (
        f"Expected a warning about missing model validation, got: {[str(w.message) for w in caught]}"
    )


@pytest.mark.parametrize("pkg", _driver_params())
def test_no_unused_examples(pkg):
    examples_dir = pkg / "examples"
    readme_path = pkg / "README.md"
    unused = find_unused_examples(examples_dir, readme_path)
    assert not unused, f"{pkg.name}: example files not referenced in README.md: {[p.name for p in unused]}"


@pytest.mark.parametrize("pkg", _driver_params())
def test_no_inline_code_blocks(pkg):
    readme_path = pkg / "README.md"
    violations = find_inline_code_blocks(readme_path)
    assert not violations, (
        f"{pkg.name}: README.md has inline code blocks that should use literalinclude: "
        f"{[f'line {line}: {desc}' for line, desc in violations]}"
    )
