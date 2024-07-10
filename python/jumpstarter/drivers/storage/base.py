from abc import abstractmethod
from .. import DriverBase


class StorageMux(DriverBase, interface="storage_mux"):
    @abstractmethod
    def host(self) -> str: ...

    @abstractmethod
    def dut(self): ...

    @abstractmethod
    def off(self): ...

    @abstractmethod
    def write(self, src: str): ...


class StorageTempdir(DriverBase, interface="storage_tempdir"):
    @abstractmethod
    def cleanup(self): ...
