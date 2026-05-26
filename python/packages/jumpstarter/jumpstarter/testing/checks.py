from __future__ import annotations

import re
from pathlib import Path

EXTRACTABLE_LANGUAGES = frozenset({"yaml", "python", "py"})
SKIP_DIRECTIVES = frozenset({
    "literalinclude", "eval-rst", "code-block", "testsetup", "testcleanup",
    "note", "warning", "tip", "mermaid", "toctree", "glossary", "tab",
    "important", "seealso", "raw", "include", "doctest", "testcode",
})


def discover_example_files(
    examples_dir: Path,
) -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    if not examples_dir.exists():
        return items
    for f in sorted(examples_dir.glob("config*.yaml")):
        items.append((f, "yaml"))
    for f in sorted(examples_dir.glob("usage*.py")):
        items.append((f, "python"))
    return items


def find_unused_examples(
    examples_dir: Path,
    readme_path: Path,
) -> list[Path]:
    if not readme_path.exists() or not examples_dir.exists():
        return []
    readme_content = readme_path.read_text(encoding="utf-8")
    return [
        path
        for path, _ in discover_example_files(examples_dir)
        if (path.name.startswith("config") or path.name.startswith("usage"))
        and path.name not in readme_content
    ]


def find_inline_code_blocks(readme_path: Path) -> list[tuple[int, str]]:
    if not readme_path.exists():
        return []
    violations: list[tuple[int, str]] = []
    for i, line in enumerate(readme_path.read_text(encoding="utf-8").splitlines()):
        m = re.match(r"^`{3,}\{?([^}`\s]+)\}?\s*(.*)", line.strip())
        if not m:
            continue
        lang = m.group(1).strip("{}").lower()
        if lang in SKIP_DIRECTIVES:
            continue
        if lang in EXTRACTABLE_LANGUAGES:
            violations.append((i + 1, f"inline ```{lang}"))
    return violations
