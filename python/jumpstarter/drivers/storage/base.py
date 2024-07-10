from abc import abstractmethod
from typing import Dict
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

    @abstractmethod
    def download(self, url: str, headers: Dict[str, str], filename: str): ...

    @abstractmethod
    def open(self, filename: str, mode: str) -> int: ...
