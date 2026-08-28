import ctypes
import signal
import socket
import sys
from base64 import b64encode
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from hashlib import sha256
from shutil import which
from subprocess import Popen, TimeoutExpired
from tempfile import gettempdir

from aiohttp import ClientSession, UnixConnector
from anyio import connect_unix
from jumpstarter_driver_video.driver import VideoInterface

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
class UStreamer(VideoInterface, Driver):
    executable: str = field(default_factory=find_ustreamer)
    args: dict[str, str] = field(default_factory=dict)

    # Where the ustreamer control socket lives. Defaults to a path DERIVED FROM
    # THE CONFIG rather than a fresh TemporaryDirectory.
    #
    # This used to be `tempdir: TemporaryDirectory = field(default_factory=...)`,
    # which made the socket path per-INSTANCE. The exporter constructs a driver
    # more than once for the same export (measured: 4 instances across 3 leases),
    # so whichever instance served a DriverCall computed a socket path belonging
    # to a different instance's ustreamer process, and every `state`/`snapshot`
    # failed intermittently with:
    #
    #   Cannot connect to unix socket /tmp/tmpXXXXXXXX/socket
    #     [No such file or directory]
    #
    # Hashing the config makes the path stable across instances while still
    # keeping two differently-configured cameras apart.
    runtime_dir: str | None = None

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_ustreamer.client.UStreamerClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        cmdline = [self.executable]

        for key, value in self.args.items():
            cmdline += [f"--{key}", value]

        # Stable, config-derived socket directory. Two UStreamer exports with
        # different args (e.g. two cameras) still get separate sockets, while
        # repeated construction of the SAME export converges on one path.
        if self.runtime_dir is None:
            digest = sha256(
                repr(sorted(self.args.items())).encode() + self.executable.encode()
            ).hexdigest()[:16]
            base = Path(gettempdir()) / f"jmp-ustreamer-{digest}"
        else:
            base = Path(self.runtime_dir)
        base.mkdir(parents=True, exist_ok=True)

        self.socketp = base / "socket"

        # Reuse a live server instead of racing a second one onto the same
        # socket. `--unix-rm` below means a starting ustreamer would delete the
        # socket the running one is serving, which is precisely the failure this
        # is meant to avoid.
        if self._socket_is_live():
            self.logger.info("Reusing ustreamer already listening on %s", self.socketp)
            self.process = None
            return

        # A socket file left behind by a dead server would make the connect
        # attempt fail with ECONNREFUSED rather than ENOENT; --unix-rm tells
        # ustreamer to clear it on startup.
        cmdline += ["--unix", str(self.socketp), "--unix-rm"]

        self.process = Popen(
            cmdline,
            stdout=sys.stdout,
            stderr=sys.stderr,
            preexec_fn=_get_preexec_fn(),
        )

    def _socket_is_live(self) -> bool:
        """True if something is accepting connections on the socket right now."""
        if not self.socketp.exists():
            return False
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(0.5)
            s.connect(str(self.socketp))
            return True
        except OSError:
            return False
        finally:
            s.close()

    def close(self):
        # None when we adopted a server started by another instance of this same
        # export -- tearing it down here would kill the stream out from under
        # whoever is still using it.
        if self.process is None:
            return

        socketp = getattr(self, "socketp", None)

        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except TimeoutExpired:
            self.process.kill()

        # ustreamer does not remove its socket file on SIGTERM. A leftover file
        # makes the NEXT instance's liveness probe fail with ECONNREFUSED instead
        # of the ENOENT it expects, which reads as a mysterious connection error
        # rather than "no server running". --unix-rm covers the same case on
        # startup; doing it here too keeps /tmp clean when the exporter stops.
        if socketp is not None:
            Path(socketp).unlink(missing_ok=True)

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

    @export
    def stream_path(self) -> str:
        return "/stream"

    @exportstream
    @asynccontextmanager
    async def connect(self):
        self.logger.debug("streaming video")
        async with await connect_unix(self.socketp) as stream:
            yield stream
