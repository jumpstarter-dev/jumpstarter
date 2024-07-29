import sys
from base64 import b64encode
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from shutil import which
from subprocess import Popen
from tempfile import TemporaryDirectory

from aiohttp import ClientSession, UnixConnector
from anyio import connect_unix

from jumpstarter.drivers import Driver, export, exportstream


def find_ustreamer():
    executable = which("ustreamer")

    if executable is None:
        raise FileNotFoundError("ustreamer executable not found")

    return executable


@dataclass(kw_only=True)
class UStreamer(Driver):
    executable: str = field(default_factory=find_ustreamer)
    args: dict[str, str] = field(default_factory=dict)
    tempdir: TemporaryDirectory = field(default_factory=TemporaryDirectory)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.drivers.video.client.UStreamerClient"

    def __post_init__(self, *args):
        cmdline = [self.executable]

        for key, value in self.args.items():
            cmdline += [f"--{key}", value]

        self.socketp = Path(self.tempdir.name) / "socket"

        cmdline += ["--unix", self.socketp]

        self.process = Popen(cmdline, stdout=sys.stdout, stderr=sys.stderr)

    def __del__(self):
        if hasattr(self, "process"):
            self.process.terminate()
            self.process.wait(timeout=5)

    @export
    async def state(self):
        sess = ClientSession(connector=UnixConnector(path=self.socketp))
        async with sess.get("http://localhost/state") as r:
            return await r.json()

    @export
    async def snapshot(self):
        sess = ClientSession(connector=UnixConnector(path=self.socketp))
        async with sess.get("http://localhost/snapshot") as r:
            return b64encode(await r.read()).decode("ascii")

    @exportstream
    @asynccontextmanager
    async def connect(self):
        async with await connect_unix(self.socketp) as stream:
            yield stream
