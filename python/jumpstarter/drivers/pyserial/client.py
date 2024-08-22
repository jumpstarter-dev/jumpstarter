from jumpstarter.client import DriverClient
from jumpstarter.client.mixins import ExpectMixin


class PySerialClient(DriverClient, ExpectMixin):
    pass
