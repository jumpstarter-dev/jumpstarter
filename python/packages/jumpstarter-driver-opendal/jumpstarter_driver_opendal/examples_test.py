from __future__ import annotations

from pathlib import Path


EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_usage_py_compiles():
    source = (EXAMPLES_DIR / "usage.py").read_text()
    compile(source, "usage.py", "exec")


def test_usage_examples_py_compiles():
    source = (EXAMPLES_DIR / "usage_examples.py").read_text()
    compile(source, "usage_examples.py", "exec")
