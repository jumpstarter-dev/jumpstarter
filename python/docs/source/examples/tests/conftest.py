import importlib.util
from pathlib import Path

import pytest


@pytest.fixture()
def examples_root():
    return Path(__file__).parent.parent


@pytest.fixture()
def driver_example_module(examples_root):
    path = examples_root / "introduction" / "driver_example.py"
    spec = importlib.util.spec_from_file_location("driver_example", str(path))
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
