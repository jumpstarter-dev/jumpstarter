"""Persistent overlay sessions using the normal exporter transport."""

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_power.driver import MockPower
from pydantic.dataclasses import dataclass

from jumpstarter.client.core import DriverMethodNotImplemented
from jumpstarter.client.overlay import prepare_client_overlay, serve_client_overlay
from jumpstarter.common import ExporterStatus
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.utils import launch_shell
from jumpstarter.config.env import JMP_DRIVERS_ALLOW, JUMPSTARTER_HOST, JUMPSTARTER_HOST_OVERLAY
from jumpstarter.exporter import Session
from jumpstarter.utils.env import env

_POWER = "jumpstarter_driver_power.driver.MockPower"


@dataclass(kw_only=True)
class LifecyclePower(MockPower):
    events: str

    def record(self, event):
        with open(self.events, "a") as f:
            f.write(event + "\n")

    def __post_init__(self):
        super().__post_init__()
        self.record("init")

    def reset(self):
        self.record("reset")
        super().reset()

    def shutdown(self):
        self.record("shutdown")
        super().shutdown()

    def close(self):
        self.record("close")
        super().close()


class RequiresPower(Composite):
    def __post_init__(self):
        super().__post_init__()
        if "power_ctl" not in self.children:
            raise ConfigurationError("RequiresPower requires 'power_ctl' child driver")
        self.children["generated"] = Composite(children={
            "power_ctl": self.children["power_ctl"],
            "local": MockPower(),
        })


@contextmanager
def _serve(driver):
    with Session(root_device=driver) as session:
        session.update_status(ExporterStatus.LEASE_READY)
        with session.serve_unix() as path:
            yield str(path)


def _overlay(tmp_path, export):
    path = tmp_path / "overlay.yaml"
    path.write_text(yaml.safe_dump({"export": export}))
    return str(path)


@pytest.fixture
def lab(monkeypatch):
    driver = Composite(children={
        "power": MockPower(children={"remote_child": MockPower()}),
        "kept": MockPower(),
    })
    with _serve(driver) as host:
        monkeypatch.setenv(JUMPSTARTER_HOST, host)
        monkeypatch.setenv(JMP_DRIVERS_ALLOW, "UNSAFE")
        monkeypatch.delenv(JUMPSTARTER_HOST_OVERLAY, raising=False)
        yield driver


def test_overlay_persists_across_processes_until_shell_exit(tmp_path, lab):
    events = tmp_path / "events"
    socket_file = tmp_path / "socket"
    overlay = _overlay(tmp_path, {
        "power": {
            "type": "jumpstarter.client.overlay_test.LifecyclePower",
            "config": {"events": str(events)},
        },
    })
    # The shell command launches two independent env() consumers, just as two
    # j invocations would. Neither client owns the local driver lifecycle.
    script = tmp_path / "shell.py"
    script.write_text(rf"""
import os, subprocess, sys
from pathlib import Path
Path({str(socket_file)!r}).write_text(os.environ['JUMPSTARTER_HOST_OVERLAY'])
for command in ('client.power.on()', 'assert client.power.status() == "on"'):
    subprocess.run([
        sys.executable, '-c',
        'from jumpstarter.utils.env import env\nwith env() as client:\n    ' + command,
    ], check=True)
    assert Path({str(events)!r}).read_text().splitlines() == ['init', 'reset']
""")
    assert launch_shell(
        os.environ[JUMPSTARTER_HOST], "lab", [], True, False,
        command=(sys.executable, str(script)), overlay=overlay,
    ) == 0
    assert events.read_text().splitlines() == ["init", "reset", "shutdown", "close"]
    assert not Path(socket_file.read_text()).exists()
    assert lab.children["power"]._state == "off"


def test_silent_whole_driver_precedence_and_listing(tmp_path, lab, monkeypatch, capsys):
    overlay = _overlay(tmp_path, {"power": {"type": _POWER}, "extra": {"type": _POWER}})
    with serve_client_overlay(overlay) as host:
        monkeypatch.setenv(JUMPSTARTER_HOST_OVERLAY, host)
        with env() as client:
            assert set(client.children) == {"power", "kept", "extra"}
            assert set(client.cli().commands) == {"power", "kept", "extra"}
            client.power.on()
            client.kept.on()
        with env() as client:
            assert client.power.status() == "on"
            assert client.kept.status() == "on"
    assert lab.children["power"]._state == "off"
    assert "replaced" not in capsys.readouterr().err


def test_refs_use_the_shell_start_snapshot(tmp_path, lab, monkeypatch):
    overlay = _overlay(tmp_path, {
        "local": {"type": _POWER},
        "alias": {"ref": "local"},
        "chain": {"ref": "alias"},
        "lab_power": {"ref": "power"},
        "board": {"children": {"pwr": {"ref": "power"}, "local": {"ref": "local"}}},
    })
    with serve_client_overlay(overlay) as host:
        monkeypatch.setenv(JUMPSTARTER_HOST_OVERLAY, host)
        Path(overlay).write_text("export: {}")
        with env() as client:
            assert client.alias is client.chain is client.local
            assert client.board.children["local"] is client.local
            assert client.lab_power is client.board.children["pwr"] is client.power
            client.alias.on()
            client.lab_power.on()
            assert client.local.status() == "on"
            assert client.power.status() == "on"


def test_generated_children_share_resolved_refs(tmp_path, lab, monkeypatch):
    overlay = _overlay(tmp_path, {
        "board": {
            "type": "jumpstarter.client.overlay_test.RequiresPower",
            "children": {"power_ctl": {"ref": "power"}},
        },
        "alias": {"ref": "board"},
        "wrapper": {
            "type": "jumpstarter.client.overlay_test.RequiresPower",
            "children": {"power_ctl": {"ref": "board"}},
        },
    })
    with serve_client_overlay(overlay) as host:
        monkeypatch.setenv(JUMPSTARTER_HOST_OVERLAY, host)
        with env() as client:
            assert client.alias is client.board
            assert client.board.power_ctl is client.power
            assert client.board.generated.power_ctl is client.power
            assert client.wrapper.power_ctl is client.board
            assert client.wrapper.generated.power_ctl is client.board
            assert client.board.generated.local.status() == "off"
            client.board.generated.power_ctl.cycle(wait=0)
    assert lab.children["power"]._state == "on"


@pytest.mark.parametrize("driver", ["MockPower", "SyncMockPower"])
def test_calls_and_streaming_calls_use_normal_rpc(tmp_path, lab, monkeypatch, driver):
    overlay = _overlay(tmp_path, {"local": {"type": f"jumpstarter_driver_power.driver.{driver}"}})
    with serve_client_overlay(overlay) as host:
        monkeypatch.setenv(JUMPSTARTER_HOST_OVERLAY, host)
        with env() as client:
            client.local.on()
            assert client.local.status() == "on"
            assert len(list(client.local.read())) == 2
            with pytest.raises(DriverMethodNotImplemented):
                client.local.call("reset")


def test_raw_streams_use_normal_rpc(tmp_path, lab, monkeypatch):
    overlay = _overlay(tmp_path, {"echo": {"type": "jumpstarter_driver_network.driver.EchoNetwork"}})
    with serve_client_overlay(overlay) as host:
        monkeypatch.setenv(JUMPSTARTER_HOST_OVERLAY, host)
        with env() as client, client.echo.stream() as stream:
            stream.send(b"hello")
            assert stream.receive() == b"hello"


def test_overlay_host_also_accepts_an_ordinary_exporter(lab, monkeypatch):
    with _serve(Composite(children={"power": Composite(), "extra": MockPower()})) as host:
        monkeypatch.setenv(JUMPSTARTER_HOST_OVERLAY, host)
        with env() as client:
            client.extra.on()
            assert client.extra.status() == "on"
            assert client.power.children == {}
            with pytest.raises(DriverMethodNotImplemented):
                client.power.call("status")


def test_external_ref_failure_does_not_fall_back_under_shadowed_driver(tmp_path, lab, monkeypatch):
    overlay = _overlay(tmp_path, {
        "power": {"type": _POWER},
        "alias": {"ref": "power.remote_child"},
    })
    with serve_client_overlay(overlay) as host:
        monkeypatch.setenv(JUMPSTARTER_HOST_OVERLAY, host)
        with pytest.raises(ConfigurationError, match="nonexistent driver 'power.remote_child'"), env():
            pass


@pytest.mark.parametrize("export", [
    {},
    {"alias": {"ref": ""}},
    {"a": {"ref": "b"}, "b": {"ref": "a"}},
    {"parent": {"children": {"loop": {"ref": "parent"}}}},
])
def test_invalid_overlay_is_rejected_before_shell_start(tmp_path, export):
    with pytest.raises(ConfigurationError):
        prepare_client_overlay(_overlay(tmp_path, export))


def test_overlay_file_validation(tmp_path):
    path = tmp_path / "overlay.yaml"
    for content in ("export: []", "bad: [", "metadata: {}\nexport: {}"):
        path.write_text(content)
        with pytest.raises(ConfigurationError, match="Failed to load"):
            prepare_client_overlay(str(path))
    path.unlink()
    with pytest.raises(ConfigurationError, match="Failed to load"):
        prepare_client_overlay(str(path))


def test_prepare_accepts_remote_refs_and_resolves_path(tmp_path):
    path = _overlay(tmp_path, {"lab_power": {"ref": "power"}})
    assert prepare_client_overlay(path) == str(Path(path).resolve())
