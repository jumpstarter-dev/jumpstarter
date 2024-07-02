from jumpstarter.drivers.base import DriverBase, drivercall
import pytest


class Driver(DriverBase):
    @property
    def interface(self):
        return "dummy"

    def invalid_drivercall(self):
        pass

    @drivercall
    def valid_drivercall(self):
        pass


def test_invalid_drivercall():
    t = Driver()
    with pytest.raises(NotImplementedError):
        t.call("invalid_drivercall", [])


def test_valid_drivercall():
    t = Driver()
    t.call("valid_drivercall", [])
