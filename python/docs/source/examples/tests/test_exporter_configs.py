from pathlib import Path

import pytest

from jumpstarter.testing.checks import discover_example_files
from jumpstarter.testing.examples import validate_example

EXAMPLES_DIR = Path(__file__).parent.parent / "introduction"


def _example_params():
    return [pytest.param(path, kind, id=path.name) for path, kind in discover_example_files(EXAMPLES_DIR)]


@pytest.mark.parametrize("path,kind", _example_params())
def test_example(path, kind):
    validate_example(path, kind)
