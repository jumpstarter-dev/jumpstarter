from __future__ import annotations

import re
from pathlib import Path

EXTRACTABLE_LANGUAGES = frozenset({"yaml", "python", "py", "bash", "shell"})
SKIP_DIRECTIVES = frozenset(
    {
        "literalinclude",
        "eval-rst",
        "code-block",
        "testsetup",
        "testcleanup",
        "note",
        "warning",
        "tip",
        "mermaid",
        "toctree",
        "glossary",
        "tab",
        "important",
        "seealso",
        "raw",
        "include",
        "doctest",
        "testcode",
    }
)


def discover_example_files(
    examples_dir: Path,
) -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    if not examples_dir.exists():
        return items
    for f in sorted(examples_dir.glob("**/*.yaml")):
        if f.name == "exporter.yaml":
            continue
        items.append((f, "yaml"))
    for f in sorted(examples_dir.glob("**/*.py")):
        items.append((f, "python"))
    return items


def _is_referenced(path: Path, examples_dir: Path, readme_content: str) -> bool:
    rel_path = str(path.relative_to(examples_dir.parent))
    if rel_path in readme_content:
        return True
    if path.parent != examples_dir:
        rel_dir = str(path.parent.relative_to(examples_dir.parent)) + "/"
        if rel_dir in readme_content:
            return True
    return False


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
        if not _is_referenced(path, examples_dir, readme_content)
    ]


def find_unused_examples_in_docs(
    examples_dir: Path,
    markdown_files: list[Path],
) -> list[Path]:
    if not examples_dir.exists():
        return []
    combined_content = "\n".join(md.read_text(encoding="utf-8") for md in markdown_files if md.exists())
    return [
        path
        for path, _ in discover_example_files(examples_dir)
        if not _is_referenced(path, examples_dir, combined_content)
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
