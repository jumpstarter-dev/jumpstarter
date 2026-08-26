import json
import os
import socket
from unittest.mock import patch

from .client import _read_tunnel_state, _remove_tunnel_state, _write_tunnel_state


def _listener():
    """A real bound listener, so 'is the tunnel up?' is answered by connecting."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


def _state_file(tmp_path):
    return patch("jumpstarter_driver_adb.client._TUNNEL_STATE_FILE", str(tmp_path / "tunnel.json"))


def test_live_tunnel_is_reused(tmp_path):
    sock, port = _listener()
    try:
        with _state_file(tmp_path):
            _write_tunnel_state("127.0.0.1", port)
            state = _read_tunnel_state()
        assert state is not None
        assert int(state["port"]) == port
    finally:
        sock.close()


def test_orphaned_tunnel_is_not_reused(tmp_path):
    """The bug this guards: a live PID does NOT mean a live tunnel.

    `j adb tunnel` orphaned by its parent shell keeps running, reparented to init,
    so os.kill(pid, 0) succeeds indefinitely. But the lease carrying the tunnel is
    gone and nothing listens on the port, so reusing it made every later `j adb`
    command fail with "cannot connect to daemon at tcp:127.0.0.1:<port>".
    Reproduced on macOS with a tunnel orphaned hours earlier.
    """
    sock, port = _listener()
    sock.close()  # port recorded, nothing listening -- exactly the orphan case

    with _state_file(tmp_path) as _:
        # os.getpid() is alive by construction, so the process check cannot help.
        _write_tunnel_state("127.0.0.1", port)
        assert _read_tunnel_state() is None


def test_stale_state_file_is_removed(tmp_path):
    """Otherwise the dead entry is re-examined on every single command."""
    sock, port = _listener()
    sock.close()

    path = tmp_path / "tunnel.json"
    with _state_file(tmp_path):
        _write_tunnel_state("127.0.0.1", port)
        assert path.exists()
        assert _read_tunnel_state() is None
        assert not path.exists()


def test_dead_process_is_not_reused(tmp_path):
    path = tmp_path / "tunnel.json"
    with _state_file(tmp_path):
        # PID 2**22 is above any Linux/macOS pid_max, so it cannot exist.
        path.write_text(json.dumps({"host": "127.0.0.1", "port": "5037", "pid": 2**22}))
        assert _read_tunnel_state() is None
        assert not path.exists()


def test_malformed_state_file_is_discarded(tmp_path):
    path = tmp_path / "tunnel.json"
    with _state_file(tmp_path):
        path.write_text("{not json")
        assert _read_tunnel_state() is None

        path.write_text(json.dumps({"host": "127.0.0.1"}))  # no port/pid
        assert _read_tunnel_state() is None

        path.write_text(json.dumps({"host": "127.0.0.1", "port": "nope", "pid": os.getpid()}))
        assert _read_tunnel_state() is None


def test_missing_state_file_is_not_an_error(tmp_path):
    with _state_file(tmp_path):
        assert _read_tunnel_state() is None
        _remove_tunnel_state()  # must not raise
