#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

import yaml

PACKAGES_DIR = Path(__file__).parent.parent / "packages"
LITERALINCLUDE_PREFIX = "../../../../../packages"


class Action(Enum):
    EXTRACT_YAML = auto()
    EXTRACT_PYTHON = auto()
    LEAVE_INLINE = auto()
    ALREADY_DONE = auto()
    REMOVE = auto()


@dataclass
class Block:
    language: str
    content: str
    line_start: int
    line_end: int
    raw_lines: list[str]
    heading: str
    is_directive: bool
    directive_name: str
    fence_marker: str


@dataclass
class Extraction:
    block: Block
    filename: str
    action: Action


@dataclass
class DriverResult:
    driver_name: str
    package_dir: Path
    module_dir: Path
    extractions: list[Extraction] = field(default_factory=list)
    yaml_files: list[str] = field(default_factory=list)
    python_files: list[str] = field(default_factory=list)


def parse_readme(readme_path: Path) -> list[Block]:
    lines = readme_path.read_text(encoding="utf-8").splitlines(keepends=True)
    blocks: list[Block] = []
    current_heading = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("#"):
            current_heading = stripped.lstrip("#").strip()
            i += 1
            continue

        fence_match = re.match(r"^(`{3,})\{?([^}`\s]*)\}?\s*(.*)", stripped)
        if not fence_match or not fence_match.group(2):
            if stripped.startswith("```") and len(stripped) == 3:
                i += 1
                continue
            i += 1
            continue

        marker = fence_match.group(1)
        raw_lang = fence_match.group(2).strip()
        extra = fence_match.group(3).strip()

        is_directive = stripped.startswith(f"{marker}{{")
        directive_name = raw_lang if is_directive else ""

        if is_directive and extra:
            language = extra.split()[0].lower()
        elif is_directive and raw_lang in ("testcode", "doctest"):
            language = "python"
        elif is_directive and raw_lang in ("code-block",):
            language = ""
        elif not is_directive:
            language = raw_lang.lower()
        else:
            language = raw_lang.lower()

        block_start = i
        raw_block_lines = [lines[i]]
        i += 1

        option_lines = []
        while i < len(lines):
            s = lines[i].strip()
            if s.startswith(":") and ":" in s[1:]:
                option_lines.append(lines[i])
                raw_block_lines.append(lines[i])
                i += 1
            else:
                break

        content_lines: list[str] = []
        while i < len(lines):
            s = lines[i].strip()
            if s.startswith(marker) and len(s) <= len(marker) + 1:
                raw_block_lines.append(lines[i])
                i += 1
                break
            content_lines.append(lines[i])
            raw_block_lines.append(lines[i])
            i += 1

        content = "".join(content_lines).strip()

        blocks.append(Block(
            language=language,
            content=content,
            line_start=block_start + 1,
            line_end=i,
            raw_lines=raw_block_lines,
            heading=current_heading,
            is_directive=is_directive,
            directive_name=directive_name,
            fence_marker=marker,
        ))

    return blocks


def classify_block(block: Block) -> Action:
    if block.directive_name == "literalinclude":
        return Action.ALREADY_DONE

    if block.directive_name in ("eval-rst", "note", "warning", "tip",
                                 "mermaid", "toctree", "glossary",
                                 "tab", "important", "seealso"):
        return Action.LEAVE_INLINE

    if block.directive_name in ("testsetup", "testcleanup"):
        return Action.REMOVE

    if block.directive_name == "doctest":
        return Action.LEAVE_INLINE

    if block.content.lstrip().startswith(">>>"):
        return Action.LEAVE_INLINE

    content_lower = block.content.lower()

    if "pip install" in content_lower or "pip3 install" in content_lower:
        return Action.LEAVE_INLINE

    if block.language in ("console", "shell", ""):
        if content_lower.startswith("$") or content_lower.startswith("usage:"):
            return Action.LEAVE_INLINE
        return Action.LEAVE_INLINE

    if block.language == "yaml":
        if len(block.content.splitlines()) < 3:
            return Action.LEAVE_INLINE
        try:
            yaml.safe_load(block.content)
        except yaml.YAMLError:
            return Action.LEAVE_INLINE
        if "export:" in block.content or "apiversion:" in content_lower or "type:" in content_lower.split("\n")[0]:
            return Action.EXTRACT_YAML
        if re.search(r"^\w+:", block.content, re.MULTILINE):
            return Action.EXTRACT_YAML
        return Action.LEAVE_INLINE

    if block.language == "python" or block.directive_name == "testcode":
        if len(block.content.splitlines()) < 2:
            return Action.LEAVE_INLINE
        return Action.EXTRACT_PYTHON

    if block.language == "bash":
        if block.content.strip().startswith("$") or block.content.strip().startswith("j "):
            return Action.LEAVE_INLINE
        if len(block.content.splitlines()) < 2:
            return Action.LEAVE_INLINE
        return Action.LEAVE_INLINE

    if block.directive_name == "code-block" and block.language == "console":
        return Action.LEAVE_INLINE

    return Action.LEAVE_INLINE


def heading_to_slug(heading: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", heading.lower()).strip("_")
    return slug[:40] if slug else "default"


def plan_extractions(blocks: list[Block], driver_name: str) -> list[Extraction]:
    extractions: list[Extraction] = []
    yaml_count = 0
    python_count = 0

    extractable_yaml = [b for b in blocks if classify_block(b) == Action.EXTRACT_YAML]
    extractable_python = [b for b in blocks if classify_block(b) == Action.EXTRACT_PYTHON]

    for block in blocks:
        action = classify_block(block)

        if action == Action.EXTRACT_YAML:
            if len(extractable_yaml) == 1:
                filename = "config.yaml"
            else:
                slug = heading_to_slug(block.heading)
                filename = f"config_{slug}.yaml" if yaml_count > 0 else "config.yaml"
            yaml_count += 1
            extractions.append(Extraction(block=block, filename=filename, action=action))

        elif action == Action.EXTRACT_PYTHON:
            if len(extractable_python) == 1:
                filename = "usage.py"
            else:
                slug = heading_to_slug(block.heading)
                filename = f"usage_{slug}.py" if python_count > 0 else "usage.py"
            python_count += 1
            extractions.append(Extraction(block=block, filename=filename, action=action))

        elif action == Action.REMOVE:
            extractions.append(Extraction(block=block, filename="", action=action))

        else:
            extractions.append(Extraction(block=block, filename="", action=action))

    return extractions


def deduplicate_filenames(extractions: list[Extraction]) -> None:
    seen: dict[str, int] = {}
    for ext in extractions:
        if not ext.filename:
            continue
        if ext.filename in seen:
            seen[ext.filename] += 1
            base, suffix = ext.filename.rsplit(".", 1)
            ext.filename = f"{base}_{seen[ext.filename]}.{suffix}"
        else:
            seen[ext.filename] = 0


def rewrite_readme(readme_path: Path, extractions: list[Extraction], driver_name: str) -> str:
    lines = readme_path.read_text(encoding="utf-8").splitlines(keepends=True)
    removals: set[int] = set()
    replacements: dict[int, list[str]] = {}

    for ext in extractions:
        if ext.action in (Action.EXTRACT_YAML, Action.EXTRACT_PYTHON):
            start_idx = ext.block.line_start - 1
            end_idx = ext.block.line_end - 1
            for j in range(start_idx, end_idx + 1):
                if j < len(lines):
                    removals.add(j)

            lang = ext.block.language or "yaml"
            lit_path = f"{LITERALINCLUDE_PREFIX}/{driver_name}/examples/{ext.filename}"
            replacement = [
                f"```{{literalinclude}} {lit_path}\n",
                f":language: {lang}\n",
                "```\n",
            ]
            replacements[start_idx] = replacement

        elif ext.action == Action.REMOVE:
            start_idx = ext.block.line_start - 1
            end_idx = ext.block.line_end - 1
            for j in range(start_idx, end_idx + 1):
                if j < len(lines):
                    removals.add(j)
            preceding = start_idx - 1
            while preceding >= 0 and lines[preceding].strip() == "":
                removals.add(preceding)
                preceding -= 1

    result: list[str] = []
    i = 0
    while i < len(lines):
        if i in replacements:
            result.extend(replacements[i])
            while i in removals and i < len(lines):
                i += 1
        elif i in removals:
            i += 1
        else:
            result.append(lines[i])
            i += 1

    text = "".join(result)
    while "\n\n\n\n" in text:
        text = text.replace("\n\n\n\n", "\n\n\n")
    if not text.endswith("\n"):
        text += "\n"
    return text


def generate_test_file(result: DriverResult) -> str:
    lines = [
        "from __future__ import annotations\n",
        "\n",
        "from pathlib import Path\n",
        "\n",
    ]

    has_yaml = bool(result.yaml_files)
    if has_yaml:
        lines.insert(3, "import yaml\n")

    lines.extend([
        "EXAMPLES_DIR = Path(__file__).parent.parent / \"examples\"\n",
        "\n",
    ])

    for yf in result.yaml_files:
        func_name = yf.replace(".", "_").replace("-", "_")
        lines.extend([
            f"\n",
            f"def test_{func_name}_is_valid_yaml():\n",
            f"    data = yaml.safe_load((EXAMPLES_DIR / \"{yf}\").read_text())\n",
            f"    assert data is not None\n",
        ])

    for pf in result.python_files:
        func_name = pf.replace(".", "_").replace("-", "_")
        lines.extend([
            f"\n",
            f"\n",
            f"def test_{func_name}_compiles():\n",
            f"    source = (EXAMPLES_DIR / \"{pf}\").read_text()\n",
            f"    compile(source, \"{pf}\", \"exec\")\n",
        ])

    return "".join(lines)


def process_driver(driver_name: str, write: bool) -> DriverResult | None:
    package_dir = PACKAGES_DIR / driver_name
    readme_path = package_dir / "README.md"
    if not readme_path.exists():
        return None

    module_name = driver_name.replace("-", "_")
    module_dir = package_dir / module_name
    if not module_dir.exists():
        return None

    examples_dir = package_dir / "examples"

    blocks = parse_readme(readme_path)
    extractions = plan_extractions(blocks, driver_name)
    deduplicate_filenames(extractions)

    result = DriverResult(
        driver_name=driver_name,
        package_dir=package_dir,
        module_dir=module_dir,
    )

    extracted = [e for e in extractions if e.action in (Action.EXTRACT_YAML, Action.EXTRACT_PYTHON)]
    if not extracted:
        return result

    for ext in extracted:
        if ext.action == Action.EXTRACT_YAML:
            result.yaml_files.append(ext.filename)
        elif ext.action == Action.EXTRACT_PYTHON:
            result.python_files.append(ext.filename)

    if write:
        examples_dir.mkdir(parents=True, exist_ok=True)

        for ext in extracted:
            filepath = examples_dir / ext.filename
            filepath.write_text(ext.block.content + "\n", encoding="utf-8")

        new_readme = rewrite_readme(readme_path, extractions, driver_name)
        readme_path.write_text(new_readme, encoding="utf-8")

        if result.yaml_files or result.python_files:
            test_content = generate_test_file(result)
            test_path = module_dir / "examples_test.py"
            test_path.write_text(test_content, encoding="utf-8")

    return result


def get_all_driver_names() -> list[str]:
    drivers = []
    for d in sorted(PACKAGES_DIR.iterdir()):
        if d.name.startswith("jumpstarter-driver-") and d.is_dir():
            readme = d / "README.md"
            if readme.exists():
                drivers.append(d.name)
    return drivers


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert driver README inline snippets to literalinclude")
    parser.add_argument("--driver", help="Process a single driver (e.g., jumpstarter-driver-yepkit)")
    parser.add_argument("--write", action="store_true", help="Apply changes (default is dry-run)")
    args = parser.parse_args()

    if args.driver:
        drivers = [args.driver]
    else:
        drivers = get_all_driver_names()

    total_yaml = 0
    total_python = 0
    total_extracted = 0
    processed = 0

    for driver_name in drivers:
        result = process_driver(driver_name, write=args.write)
        if result is None:
            continue

        n_yaml = len(result.yaml_files)
        n_python = len(result.python_files)
        n_total = n_yaml + n_python

        if n_total > 0:
            processed += 1
            total_yaml += n_yaml
            total_python += n_python
            total_extracted += n_total

            mode = "WROTE" if args.write else "DRY-RUN"
            files_list = ", ".join(result.yaml_files + result.python_files)
            print(f"[{mode}] {driver_name}: {n_total} extracted ({n_yaml} yaml, {n_python} python) -> {files_list}")

    print(f"\nSummary: {processed} drivers, {total_extracted} files ({total_yaml} yaml, {total_python} python)")
    if not args.write:
        print("Run with --write to apply changes.")


if __name__ == "__main__":
    main()
