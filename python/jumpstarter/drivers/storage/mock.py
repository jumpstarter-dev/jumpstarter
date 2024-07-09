from . import StorageMux
from .mixin import StorageMuxLocalWriterMixin


class MockStorageMux(StorageMuxLocalWriterMixin, StorageMux):
    def host(self) -> str:
        return "/dev/null"

    def dut(self):
        pass

    def off(self):
        pass
