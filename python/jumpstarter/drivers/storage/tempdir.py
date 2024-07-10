from . import StorageTempdir
from dataclasses import dataclass, field
from tempfile import TemporaryDirectory
from pathlib import Path
from typing import Dict
import httpx


@dataclass(kw_only=True)
class LocalStorageTempdir(StorageTempdir):
    tempdir: TemporaryDirectory = field(default_factory=TemporaryDirectory, init=False)

    def cleanup(self):
        self.tempdir.cleanup()

    def resolve(self, filename: str) -> Path:
        rootpath = Path(self.tempdir.name)

        filepath = rootpath.joinpath(filename).resolve()
        filepath.relative_to(rootpath)  # prevent path traversal

        return filepath

    def download(self, url: str, headers: Dict[str, str], filename: str):
        filepath = self.resolve(filename)

        with filepath.open(mode="wb") as f:
            with httpx.stream("GET", url, headers=headers) as r:
                for chunk in r.iter_bytes():
                    f.write(chunk)

    def open(self, filename: str, mode: str) -> int:
        filepath = self.resolve(filename)

        file = filepath.open(mode=mode)
        fd = len(self.session.fds)
        self.session.fds.insert(fd, file)

        return fd
