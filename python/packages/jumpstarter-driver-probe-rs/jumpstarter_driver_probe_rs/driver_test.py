from .driver import ProbeRs
from jumpstarter.common.utils import serve


def test_drivers_probe_rs(monkeypatch):
    instance = ProbeRs()

    def mock_run_cmd(cmd):
        if cmd[0] == "info":
            return "Target: nRF52840_xxAA\nFlash size: 1MB"
        elif cmd[0] == "read":
            return "DEADBEEF CAFEBABE\nCAFE0000 DEAD0000"
        return "ok"

    monkeypatch.setattr(instance, "_run_cmd", mock_run_cmd)

    with serve(instance) as client:
        info = client.info()
        assert "Target:" in info
        assert "Flash size:" in info
        assert client.reset() == "ok"
        assert client.erase() == "ok"
        assert client.download_file("/dev/null") == "ok"
        assert client.read(32, 0xF000, 4) == [0xDEADBEEF, 0xCAFEBABE, 0xCAFE0000, 0xDEAD0000]


def test_drivers_probe_rs_errors(monkeypatch):
    instance = ProbeRs()

    monkeypatch.setattr(instance, "_run_cmd", lambda cmd: "")  # Simulate error response

    with serve(instance) as client:
        assert client.info() == ""  # Error case
        assert client.reset() == ""  # Error case
