from jumpstarter.client import DriverClient
from jumpstarter.client.mixins import StreamMixin


class NetworkClient(DriverClient, StreamMixin):
    pass
