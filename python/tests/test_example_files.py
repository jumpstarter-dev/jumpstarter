from __future__ import annotations

from pathlib import Path

import pytest

from jumpstarter.testing.checks import (
    discover_example_files,
    find_inline_code_blocks,
    find_unused_examples,
)
from jumpstarter.testing.examples import instantiate_yaml_example, validate_example

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
    validate_example(path, kind)



@pytest.mark.parametrize("path,kind", _example_file_params())
def test_example_instantiates(path, kind):
    if kind != "yaml":
        pytest.skip("not a YAML example")
    instantiate_yaml_example(path)


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
