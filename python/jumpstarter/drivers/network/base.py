from abc import abstractmethod
from .. import DriverBase


class Network(DriverBase, interface="network"):
    @abstractmethod
    def connect(self, network: str, address: str) -> int: ...
