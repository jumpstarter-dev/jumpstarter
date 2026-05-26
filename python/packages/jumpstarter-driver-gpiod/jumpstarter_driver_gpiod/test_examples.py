from pathlib import Path

import pytest
from jumpstarter.testing.checks import (
    discover_example_files,
    find_inline_code_blocks,
    find_unused_examples,
)

PACKAGE_DIR = Path(__file__).parent.parent
EXAMPLES_DIR = PACKAGE_DIR / "examples"
README_PATH = PACKAGE_DIR / "README.md"


def _example_params():
    return [pytest.param(path, kind, id=path.name) for path, kind in discover_example_files(EXAMPLES_DIR)]


@pytest.mark.parametrize("path,kind", _example_params())
def test_example(path, kind):
    testing = pytest.importorskip("jumpstarter.testing.examples")
    testing.validate_example(path, kind)


def test_no_unused_examples():
    unused = find_unused_examples(EXAMPLES_DIR, README_PATH)
    assert not unused, f"example files not referenced in README.md: {[p.name for p in unused]}"


def test_no_inline_code_blocks():
    violations = find_inline_code_blocks(README_PATH)
    assert not violations, (
        f"README.md has inline code blocks that should use literalinclude: "
        f"{[f'line {line}: {desc}' for line, desc in violations]}"
    )
