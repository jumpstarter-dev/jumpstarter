from jumpstarter.drivers import DriverClient
from jumpstarter.drivers.mixins import StreamMixin


class PySerialClient(DriverClient, StreamMixin):
    pass
