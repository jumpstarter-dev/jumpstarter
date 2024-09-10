from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


@contextmanager
def TemporarySocket():
    with TemporaryDirectory() as tempdir:
        yield Path(tempdir) / "socket"
