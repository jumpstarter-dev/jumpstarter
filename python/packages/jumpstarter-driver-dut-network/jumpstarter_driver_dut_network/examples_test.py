from pathlib import Path

import pytest

jumpstarter_testing = pytest.importorskip("jumpstarter.testing.examples")

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


@pytest.mark.parametrize(
    "path,kind", jumpstarter_testing.make_example_test_params(EXAMPLES_DIR)
)
def test_example(path, kind):
    jumpstarter_testing.validate_example(path, kind)
