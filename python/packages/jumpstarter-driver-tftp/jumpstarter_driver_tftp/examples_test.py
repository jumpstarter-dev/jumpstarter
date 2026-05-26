from __future__ import annotations

import re
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).parent.parent
EXAMPLES_DIR = PACKAGE_DIR / "examples"
README_PATH = PACKAGE_DIR / "README.md"

EXTRACTABLE_LANGUAGES = frozenset({"yaml", "python", "py"})
SKIP_DIRECTIVES = frozenset({
    "literalinclude", "eval-rst", "code-block", "testsetup", "testcleanup",
    "note", "warning", "tip", "mermaid", "toctree", "glossary", "tab",
    "important", "seealso", "raw", "include", "doctest", "testcode",
})


def _discover_example_files():
    items = []
    if not EXAMPLES_DIR.exists():
        return items
    for f in sorted(EXAMPLES_DIR.glob("**/*.yaml")):
        if f.name == "exporter.yaml":
            continue
        items.append((f, "yaml"))
    for f in sorted(EXAMPLES_DIR.glob("**/*.py")):
        items.append((f, "python"))
    return items


def _example_params():
    return [pytest.param(path, kind, id=path.name) for path, kind in _discover_example_files()]


@pytest.mark.parametrize("path,kind", _example_params())
def test_example(path, kind):
    testing = pytest.importorskip("jumpstarter.testing.examples")
    testing.validate_example(path, kind)


def test_no_unused_examples():
    if not README_PATH.exists():
        pytest.skip("no README.md")
    readme_content = README_PATH.read_text(encoding="utf-8")
    unused = []
    for path, _ in _discover_example_files():
        if not (path.name.startswith("config") or path.name.startswith("usage")):
            continue
        if path.name not in readme_content:
            unused.append(path)
    assert not unused, f"example files not referenced in README.md: {[p.name for p in unused]}"


def test_no_inline_code_blocks():
    if not README_PATH.exists():
        pytest.skip("no README.md")
    violations = []
    for i, line in enumerate(README_PATH.read_text(encoding="utf-8").splitlines()):
        m = re.match(r"^`{3,}\{?([^}`\s]+)\}?\s*(.*)", line.strip())
        if not m:
            continue
        lang = m.group(1).strip("{}").lower()
        if lang in SKIP_DIRECTIVES:
            continue
        if lang in EXTRACTABLE_LANGUAGES:
            violations.append((i + 1, f"inline ```{lang}"))
    assert not violations, (
        f"README.md has inline code blocks that should use literalinclude: "
        f"{[f'line {line}: {desc}' for line, desc in violations]}"
    )
