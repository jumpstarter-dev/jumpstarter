from shutil import copyfileobj
import os


class StorageMuxLocalWriterMixin:
    def write(self, src: int):
        dstpath = self.host()
        import time

        time.sleep(3)
        fd = os.open(dstpath, os.O_WRONLY)
        with os.fdopen(fd, "wb") as dst:
            copyfileobj(self.session.fds[int(src)], dst)
