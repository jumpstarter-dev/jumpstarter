from contextlib import AbstractContextManager

from .adapters import DbusAdapter
from .driver import DbusNetwork
from jumpstarter.client import DriverClient


class NetworkClient(DriverClient):
    pass


class DbusNetworkClient(NetworkClient, AbstractContextManager):
    def __enter__(self):
        self.adapter = DbusAdapter(client=self)
        self.adapter.__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        self.adapter.__exit__(exc_type, exc_value, traceback)

    @property
    def kind(self):
        return self.labels[DbusNetwork.KIND_LABEL]
