from __future__ import annotations

import yaml as _yaml

from jumpstarter.testing.checks import (
    _is_referenced,
    discover_example_files,
    find_inline_code_blocks,
    find_unused_examples,
    find_unused_examples_in_docs,
)


def _write_yaml(path, data):
    path.write_text(_yaml.dump(data), encoding="utf-8")


def test_discover_example_files_returns_yaml_and_python(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    _write_yaml(examples / "config.yaml", {"key": "value"})
    (examples / "usage.py").write_text("pass\n", encoding="utf-8")
    result = discover_example_files(examples)
    assert len(result) == 2
    kinds = {kind for _, kind in result}
    assert kinds == {"yaml", "python"}


def test_discover_example_files_skips_exporter_yaml(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    _write_yaml(examples / "exporter.yaml", {"key": "value"})
    _write_yaml(examples / "config.yaml", {"key": "value"})
    result = discover_example_files(examples)
    names = [p.name for p, _ in result]
    assert "exporter.yaml" not in names
    assert "config.yaml" in names


def test_discover_example_files_empty_for_missing_dir(tmp_path):
    assert discover_example_files(tmp_path / "nonexistent") == []


def test_discover_example_files_finds_nested(tmp_path):
    examples = tmp_path / "examples"
    subdir = examples / "scenarios"
    subdir.mkdir(parents=True)
    _write_yaml(subdir / "full.yaml", {"key": "value"})
    result = discover_example_files(examples)
    assert len(result) == 1
    assert result[0][0].name == "full.yaml"


def test_is_referenced_by_relative_path(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    f = examples / "config.yaml"
    _write_yaml(f, {"key": "value"})
    assert _is_referenced(f, examples, "see examples/config.yaml for details")


def test_is_referenced_by_directory(tmp_path):
    examples = tmp_path / "examples"
    subdir = examples / "scenarios"
    subdir.mkdir(parents=True)
    f = subdir / "full.yaml"
    _write_yaml(f, {"key": "value"})
    assert _is_referenced(f, examples, "see examples/scenarios/ for details")


def test_is_referenced_rejects_unrelated_content(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    f = examples / "config.yaml"
    _write_yaml(f, {"key": "value"})
    assert not _is_referenced(f, examples, "no reference here at all")


def test_find_unused_examples_returns_unreferenced(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    _write_yaml(examples / "config.yaml", {"key": "value"})
    _write_yaml(examples / "other.yaml", {"key": "value"})
    readme = tmp_path / "README.md"
    readme.write_text("see examples/config.yaml\n", encoding="utf-8")
    unused = find_unused_examples(examples, readme)
    assert len(unused) == 1
    assert unused[0].name == "other.yaml"


def test_find_unused_examples_empty_when_no_readme(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    _write_yaml(examples / "config.yaml", {"key": "value"})
    assert find_unused_examples(examples, tmp_path / "README.md") == []


def test_find_unused_examples_in_docs_returns_empty_for_no_markdown_files(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    _write_yaml(examples / "config.yaml", {"key": "value"})
    unused = find_unused_examples_in_docs(examples, [])
    assert len(unused) == 1
    assert unused[0].name == "config.yaml"


def test_find_unused_examples_in_docs_finds_reference_across_multiple_files(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    _write_yaml(examples / "config.yaml", {"key": "value"})
    _write_yaml(examples / "other.yaml", {"key": "value"})

    doc1 = tmp_path / "doc1.md"
    doc1.write_text("see examples/config.yaml for details\n", encoding="utf-8")
    doc2 = tmp_path / "doc2.md"
    doc2.write_text("see examples/other.yaml for details\n", encoding="utf-8")

    unused = find_unused_examples_in_docs(examples, [doc1, doc2])
    assert unused == []


def test_find_unused_examples_in_docs_returns_unreferenced(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    _write_yaml(examples / "config.yaml", {"key": "value"})
    _write_yaml(examples / "unused.yaml", {"key": "value"})

    doc = tmp_path / "doc.md"
    doc.write_text("see examples/config.yaml\n", encoding="utf-8")

    unused = find_unused_examples_in_docs(examples, [doc])
    assert len(unused) == 1
    assert unused[0].name == "unused.yaml"


def test_find_unused_examples_in_docs_returns_empty_for_missing_dir(tmp_path):
    assert find_unused_examples_in_docs(tmp_path / "nonexistent", []) == []


def test_discover_example_files_returns_bash(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "usage_cli.bash").write_text("echo hello\n", encoding="utf-8")
    result = discover_example_files(examples)
    assert len(result) == 1
    assert result[0] == (examples / "usage_cli.bash", "bash")


def test_discover_example_files_returns_all_kinds(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    _write_yaml(examples / "config.yaml", {"key": "value"})
    (examples / "usage.py").write_text("pass\n", encoding="utf-8")
    (examples / "usage_cli.bash").write_text("echo hello\n", encoding="utf-8")
    result = discover_example_files(examples)
    assert len(result) == 3
    kinds = {kind for _, kind in result}
    assert kinds == {"yaml", "python", "bash"}


def test_find_unused_examples_detects_unreferenced_bash(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "usage_cli.bash").write_text("echo hello\n", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text("no reference here\n", encoding="utf-8")
    unused = find_unused_examples(examples, readme)
    assert len(unused) == 1
    assert unused[0].name == "usage_cli.bash"


def test_find_inline_code_blocks_detects_yaml(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("```yaml\nkey: value\n```\n", encoding="utf-8")
    violations = find_inline_code_blocks(readme)
    assert len(violations) == 1
    assert violations[0][0] == 1


def test_find_inline_code_blocks_detects_python(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("```python\npass\n```\n", encoding="utf-8")
    violations = find_inline_code_blocks(readme)
    assert len(violations) == 1


def test_find_inline_code_blocks_ignores_literalinclude(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("```{literalinclude} examples/config.yaml\n```\n", encoding="utf-8")
    assert find_inline_code_blocks(readme) == []


def test_find_inline_code_blocks_ignores_mermaid(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("```mermaid\ngraph TD\n```\n", encoding="utf-8")
    assert find_inline_code_blocks(readme) == []


def test_find_inline_code_blocks_empty_for_missing_file(tmp_path):
    assert find_inline_code_blocks(tmp_path / "README.md") == []


def test_find_inline_code_blocks_detects_bash(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("```bash\necho hello\n```\n", encoding="utf-8")
    violations = find_inline_code_blocks(readme)
    assert len(violations) == 1
    assert violations[0][0] == 1


def test_find_inline_code_blocks_detects_shell(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("```shell\nls -la\n```\n", encoding="utf-8")
    violations = find_inline_code_blocks(readme)
    assert len(violations) == 1
    assert violations[0][0] == 1
