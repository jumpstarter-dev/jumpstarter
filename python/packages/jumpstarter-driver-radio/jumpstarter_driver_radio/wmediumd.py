import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from jumpstarter_driver_network.driver import TcpNetwork

from .common import RadioPort
from .driver import RadioInterface
from jumpstarter.driver import Driver, export

AGENT = Path(__file__).with_name("wmediumd_agent.py")


class FrameBridgeError(Exception):
    pass


@dataclass(kw_only=True)
class WmediumdWifi(RadioInterface, Driver):
    """The Wi-Fi of a device whose medium is wmediumd (Cuttlefish, or any ``mac80211_hwsim`` setup that uses it).

    The driver runs a small bridge beside the medium and offers its frame port, ``hwsim``, as the child
    ``frames``: a TCP stream of length-prefixed 802.11 frames. Joining the ``hwsim`` ports of two devices puts
    their Wi-Fi on one air.
    """

    # The medium's API socket, a glob. It is looked for until it is there, so the bridge may be started before
    # the device is powered on, and finds the medium again after a power cycle.
    api_socket: str = "/tmp/cf_env_*/env-*/wmediumd_api_server"
    # Where the frame port listens. Loopback: it is reached through the lease, or by a forward.
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

    _process: subprocess.Popen | None = field(init=False, default=None, repr=False)
    _state: dict = field(init=False, default_factory=dict, repr=False)
    _reader: threading.Thread | None = field(init=False, default=None, repr=False)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        self.children["frames"] = TcpNetwork(host=self.host, port=self.port)

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

    @export
    def ports(self) -> list[RadioPort]:
        return [RadioPort(name="hwsim", direction="connects", protocol="ieee80211-frames", child="frames")]

    def close(self):
        self.stop()
        super().close()
