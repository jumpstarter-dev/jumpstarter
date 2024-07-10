from . import StorageTempdir
from dataclasses import dataclass, field
from tempfile import TemporaryDirectory


@dataclass(kw_only=True)
class LocalStorageTempdir(StorageTempdir):
    tempdir: TemporaryDirectory = field(default_factory=TemporaryDirectory, init=False)

    def cleanup(self):
        self.tempdir.cleanup()
