from pathlib import Path

import pytest


@pytest.fixture()
def examples_root():
    return Path(__file__).parent.parent
