import pytest
from jumpstarter_driver_pyserial.driver import PySerial

from jumpstarter.common.utils import serve


@pytest.fixture
def pyserial():
    with serve(PySerial(url="loop://")) as client:
        yield client


@pytest.fixture(autouse=True)
def drivers_namespace(doctest_namespace, pyserial):
    doctest_namespace["pyserial"] = pyserial
