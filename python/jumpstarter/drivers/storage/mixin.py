from shutil import copyfile


class StorageMuxLocalWriterMixin:
    def write(self, src: str):
        dst = self.host()
        copyfile(src, dst)
