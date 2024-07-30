from jumpstarter.drivers import DriverClient
from jumpstarter.drivers.mixins import ExpectMixin


class PySerialClient(DriverClient, ExpectMixin):
    pass
