from __future__ import annotations

from pathlib import Path

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_usage_py_compiles():
    source = (EXAMPLES_DIR / "usage.py").read_text()
    compile(source, "usage.py", "exec")


def test_usage_flash_multiple_partitions_py_compiles():
    source = (EXAMPLES_DIR / "usage_flash_multiple_partitions.py").read_text()
    compile(source, "usage_flash_multiple_partitions.py", "exec")


def test_usage_flash_with_compressed_images_py_compiles():
    source = (EXAMPLES_DIR / "usage_flash_with_compressed_images.py").read_text()
    compile(source, "usage_flash_with_compressed_images.py", "exec")


def test_usage_power_control_py_compiles():
    source = (EXAMPLES_DIR / "usage_power_control.py").read_text()
    compile(source, "usage_power_control.py", "exec")
