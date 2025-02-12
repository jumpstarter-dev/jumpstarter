from .driver import DbusNetwork
from jumpstarter.client import DriverClient


class NetworkClient(DriverClient):
    pass


class DbusNetworkClient(NetworkClient):
    @property
    def kind(self):
        return self.labels[DbusNetwork.KIND_LABEL]
