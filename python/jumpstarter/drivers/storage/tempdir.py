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

    def download(self, url: str, headers: Dict[str, str], filename: str):
        rootpath = Path(self.tempdir.name)

        filepath = rootpath.joinpath(filename).resolve()
        filepath.relative_to(rootpath)  # prevent path traversal

        with filepath.open(mode="wb") as f:
            with httpx.stream("GET", url, headers=headers) as r:
                for chunk in r.iter_bytes():
                    f.write(chunk)
