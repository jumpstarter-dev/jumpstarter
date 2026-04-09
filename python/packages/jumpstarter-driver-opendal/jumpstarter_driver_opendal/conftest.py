import pytest

from .driver import Opendal
from jumpstarter.common.utils import serve


@pytest.fixture
def opendal(tmp_path):
    with serve(Opendal(scheme="fs", kwargs={"root": str(tmp_path)})) as client:
        yield client


@pytest.fixture(autouse=True)
def opendal_namespace(doctest_namespace, opendal, tmp_path):
    doctest_namespace["opendal"] = opendal
    doctest_namespace["tmp"] = tmp_path
