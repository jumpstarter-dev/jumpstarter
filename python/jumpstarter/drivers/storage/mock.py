from . import StorageMux
from .mixin import StorageMuxLocalWriterMixin


class MockStorageMux(StorageMuxLocalWriterMixin, StorageMux):
    def host(self) -> str:
        return "/tmp/mock"

    def dut(self):
        pass

    def off(self):
        pass
