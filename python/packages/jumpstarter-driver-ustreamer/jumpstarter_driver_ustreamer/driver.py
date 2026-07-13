import ctypes
import signal
import sys
from base64 import b64encode
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from shutil import which
from subprocess import Popen, TimeoutExpired
from tempfile import TemporaryDirectory

from aiohttp import ClientSession, UnixConnector
from anyio import connect_unix

from .common import UStreamerState
from jumpstarter.driver import Driver, export, exportstream

_IS_LINUX = sys.platform.startswith("linux")


def find_ustreamer():
    executable = which("ustreamer")

    if executable is None:
        raise FileNotFoundError("ustreamer executable not found")

    return executable


def _get_preexec_fn() -> Callable[[], None] | None:
    """Get platform-specific preexec_fn for the ustreamer subprocess.

    On Linux, returns a function that sets PR_SET_PDEATHSIG to SIGTERM,
    ensuring ustreamer receives SIGTERM when the parent process dies.
    This works even if the parent is killed with SIGKILL.

    On other platforms, returns None.
    """
    if not _IS_LINUX:
        return None

    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    PR_SET_PDEATHSIG = 1

    def set_pdeathsig():
        """Set parent death signal to SIGTERM via prctl."""
        if libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0) != 0:
            errno = ctypes.get_errno()
            raise OSError(errno, "prctl(PR_SET_PDEATHSIG) failed")

    return set_pdeathsig


@dataclass(kw_only=True)
class UStreamer(Driver):
    executable: str = field(default_factory=find_ustreamer)
    args: dict[str, str] = field(default_factory=dict)
    tempdir: TemporaryDirectory = field(default_factory=TemporaryDirectory)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_ustreamer.client.UStreamerClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        cmdline = [self.executable]

        for key, value in self.args.items():
            cmdline += [f"--{key}", value]

        self.socketp = Path(self.tempdir.name) / "socket"

        cmdline += ["--unix", self.socketp]

        self.process = Popen(
            cmdline,
            stdout=sys.stdout,
            stderr=sys.stderr,
            preexec_fn=_get_preexec_fn(),
        )

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except TimeoutExpired:
            self.process.kill()

    @export
    async def state(self):
        async with ClientSession(connector=UnixConnector(path=self.socketp)) as session:
            async with session.get("http://localhost/state") as r:
                json = await r.json()
                self.logger.debug(f"state: {json}")
                return UStreamerState.model_validate(json)

    @export
    async def snapshot(self):
        async with ClientSession(connector=UnixConnector(path=self.socketp)) as session:
            async with session.get("http://localhost/snapshot") as r:
                data = await r.read()
                length = len(data)
                self.logger.debug(f"snapshot: {length} bytes")
                return b64encode(data).decode("ascii")

    @exportstream
    @asynccontextmanager
    async def connect(self):
        self.logger.debug("streaming video")
        async with await connect_unix(self.socketp) as stream:
            yield stream
