import pytest

from jumpstarter.common.utils import serve


@pytest.fixture(autouse=True)
def namespace(doctest_namespace):
    doctest_namespace["serve"] = serve
