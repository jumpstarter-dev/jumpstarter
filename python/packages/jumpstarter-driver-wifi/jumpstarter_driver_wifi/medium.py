import json
import os
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import anyio
from anyio import to_thread
from anyio.abc import ObjectStream, SocketStream

from .interface import WifiDriver, only_port
from jumpstarter.driver import Port, PortDirection, export

AGENT = Path(__file__).with_name("wmediumd_agent.py")


PROTOCOL = "ieee80211-frames"


class FrameBridgeError(Exception):
    pass


class _FramePort(ObjectStream[bytes]):
    """The bridge's frame port, for one forwarded connection.

    It connects when the exporter first reads or writes it, which is when the far end has joined the path, and
    it waits for a bridge that is not up yet. So the bridge has a peer only while there is one, and a path
    that opens before the bridge is started (or across a power cycle of the device) is not opened and closed
    over and over while it waits.
    """

    def __init__(self, address: tuple[str, int], retry: float) -> None:
        self.address, self.retry = address, retry
        self.stream: SocketStream | None = None
        self.opening = anyio.Lock()

    async def _open(self) -> SocketStream:
        async with self.opening:
            while self.stream is None:
                try:
                    self.stream = await anyio.connect_tcp(*self.address)
                except OSError:
                    await anyio.sleep(self.retry)
            return self.stream

    async def send(self, item: bytes) -> None:
        await (await self._open()).send(item)

    async def receive(self) -> bytes:
        return await (await self._open()).receive()

    async def send_eof(self) -> None:
        if self.stream is not None:
            await self.stream.send_eof()

    async def aclose(self) -> None:
        if self.stream is not None:
            await self.stream.aclose()


@dataclass(kw_only=True)
class WifiMedium(WifiDriver):
    """The Wi-Fi of a virtual device, joined at its simulated medium: wmediumd (Cuttlefish, or any
    ``mac80211_hwsim`` setup that uses it).

    The driver runs a small bridge beside the medium and declares its frame port, ``hwsim``: a ``connects``
    port that carries a stream of length-prefixed 802.11 frames. A forward between the ``hwsim`` ports of two
    devices is a path, and puts their Wi-Fi on one air.
    """

    # The medium's API socket, a glob. It is looked for until it is there, so the bridge may be started before
    # the device is powered on, and finds the medium again after a power cycle.
    api_socket: str = "/tmp/cf_env_*/env-*/wmediumd_api_server"
    # Where the bridge's frame port listens. Loopback: the exporter connects to it for the forward.
    host: str = "127.0.0.1"
    port: int = 7310
    # A station of the medium's configuration that no device uses: the bridge sends as it. Cuttlefish
    # configures stations for many instances; this is the first radio of its second.
    station: str = "02:15:b3:00:00:00"
    # Senders that are not passed on, as address prefixes. Two Cuttlefish devices have the same access point
    # and the same factory addresses, which must not meet; the randomized addresses Android uses do not clash.
    ignore: list[str] = field(default_factory=lambda: ["02:00:00:00", "02:15:b2"])
    # Where the medium's socket is not in the exporter's own container, the bridge is run through
    # jumpstarter-exec over this launcher socket (as the provisioners set it up). Empty: run it here.
    launcher_socket: str = field(default_factory=lambda: os.environ.get("JUMPSTARTER_LAUNCHER_SOCKET", ""))
    python: str = "python3"
    # Seconds between looks for a bridge that is not up, while a path waits for it.
    retry: float = 1.0
    # Start with the exporter's session for a lease, so that a forward that is there finds the radio's end
    # running, and no test has to start it. False: only `start` does.
    autostart: bool = True

    _process: subprocess.Popen | None = field(init=False, default=None, repr=False)
    _state: dict = field(init=False, default_factory=dict, repr=False)
    _reader: threading.Thread | None = field(init=False, default=None, repr=False)

    CAPABILITIES = ("link",)

    def __post_init__(self):
        if not self.ports:
            self.ports = [Port(name="hwsim", direction=PortDirection.CONNECTS, protocol=PROTOCOL)]
        super().__post_init__()
        only_port(self, bridged=True)

    @asynccontextmanager
    async def serving(self):
        async with super().serving():
            if self.autostart:
                try:
                    await to_thread.run_sync(self.start)
                except FrameBridgeError as error:
                    self.logger.warning("The Wi-Fi bridge did not start with the session: %s", error)
            yield  # (the session closes the driver when it ends, which stops the bridge)

    def port_address(self, port: Port) -> tuple[str, int] | None:
        return port.connect_address or (self.host, self.port)

    @asynccontextmanager
    async def connect_port(self, port: Port):
        async with _FramePort(self.port_address(port), self.retry) as frames:  # ty: ignore[invalid-argument-type]
            yield frames

    def _argv(self) -> list[str]:
        options = [
            "--api-socket", self.api_socket,
            "--listen", f"{self.host}:{self.port}",
            "--station", self.station,
            "--ignore", ",".join(self.ignore),
        ]  # fmt: skip
        if not self.launcher_socket:
            return [sys.executable, str(AGENT), *options]
        # The other container has its own Python and none of this package: hand it the bridge's source.
        launcher = Path(self.launcher_socket)
        return [
            str(launcher.parent / "jumpstarter-exec"), "exec", "--socket", str(launcher), "--",
            self.python, "-c", AGENT.read_text(), *options,
        ]  # fmt: skip

    def _follow(self, process: subprocess.Popen) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            try:
                self._state = json.loads(line)
            except ValueError:
                self.logger.debug("frame bridge: %s", line.rstrip())

    @export
    def start(self) -> None:
        """Start the bridge. It waits for the medium by itself; starting it twice does nothing."""
        if self._process is not None and self._process.poll() is None:
            return
        self._state = {}
        try:
            self._process = subprocess.Popen(
                self._argv(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
        except OSError as error:
            raise FrameBridgeError(f"cannot start the frame bridge: {error}") from error
        self._reader = threading.Thread(target=self._follow, args=(self._process,), daemon=True)
        self._reader.start()

    @export
    def stop(self) -> None:
        """Stop the bridge: the frame port closes, and the medium is as it was without it."""
        process, self._process = self._process, None
        if process is None:
            return
        process.terminate()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)

    @export
    def status(self) -> dict:
        """What the bridge last said of itself: whether it has the medium and a peer, and its frame counts."""
        if self._process is None:
            return {"running": False}
        code = self._process.poll()
        if code is not None:
            error = (self._process.stderr.read() if self._process.stderr else "").strip().splitlines()
            return {"running": False, "exit_code": code, "error": error[-1] if error else ""}
        return {"running": True, **self._state}

    def close(self):
        self.stop()
        super().close()
