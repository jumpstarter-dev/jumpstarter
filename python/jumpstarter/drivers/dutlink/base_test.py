from jumpstarter.common.utils import serve
from jumpstarter.drivers.dutlink.base import Dutlink


def test_drivers_dutlink():
    with serve(Dutlink(name="dutlink")) as client:
        pass
