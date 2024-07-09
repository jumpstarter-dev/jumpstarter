from . import StorageMux


class MockStorageMux(StorageMux):
    def host(self) -> str:
        return "/dev/null"

    def dut(self):
        pass

    def off(self):
        pass
