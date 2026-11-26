from __future__ import annotations

from datetime import timedelta

import pytest
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_power.driver import MockPower

from . import context, sessions
from .conftest import write
from .session_library import Session, SessionConfig, session_config
from .testing import use_session_factory
from jumpstarter.common.utils import serve
from jumpstarter.config.env import JUMPSTARTER_HOST

POWER = "jumpstarter_driver_power.client.PowerClient"


@pytest.fixture
def trees():
    with (
        serve(Composite(children={"device": Composite(children={"power": MockPower()})})) as android,
        serve(Composite(children={"device": Composite(children={"power": MockPower()})})) as ios,
    ):
        yield {"android": android, "ios": ios}


@pytest.fixture
def local(trees):
    """Open "local exporters" by handing out the served tree named after the session."""
    opened = []

    def open_local(name, config, config_dir=None):
        opened.append((name, config))
        return sessions.Session.from_client(trees[name], name, exporter=f"{name}-exporter")

    with use_session_factory(local=open_local):
        yield opened


class TestHelpers:
    def test_session_config(self):
        config = session_config(
            "",
            selector=" board=rpi4 ",
            exporter_config="",
            duration="1 h",
            acquisition_timeout="30 s",
            release_grace="0",
        )
        assert config.name == "default"
        assert config.selector == "board=rpi4"
        assert config.exporter_config is None
        assert config.duration == timedelta(hours=1)
        assert config.acquisition_timeout == timedelta(seconds=30)
        assert config.release_grace == 0
        assert session_config("x", duration="").duration == timedelta(minutes=30)


class TestSessionConfig:
    def test_sources_in_order(self, monkeypatch):
        calls = []
        monkeypatch.setattr(sessions, "open_shell", lambda name: calls.append(("shell", name)) or "shell")
        monkeypatch.setattr(sessions, "open_lease", lambda name, **kw: calls.append(("lease", name, kw)) or "lease")
        monkeypatch.setattr(sessions, "open_local", lambda name, **kw: calls.append(("local", name, kw)) or "local")
        both = SessionConfig(name="s", selector="a=b", exporter_config="e.yaml")
        assert both.open() == "lease"
        assert both.describe() == "a lease (a=b)"
        assert SessionConfig(name="s", exporter_config="e.yaml").open() == "local"
        assert SessionConfig(name="s", exporter_config="e.yaml").describe() == "the local exporter e.yaml"
        with pytest.raises(sessions.NoSession, match="nothing to connect to"):
            SessionConfig(name="s").open()
        assert not SessionConfig(name="s").configured
        monkeypatch.setenv(JUMPSTARTER_HOST, "/tmp/shell.sock")
        assert both.open() == "shell"
        assert both.describe() == "the jmp shell session"
        assert SessionConfig(name="s").configured
        assert calls[0][:2] == ("lease", "s")
        assert calls[0][2]["selector"] == "a=b"
        assert calls[1] == ("local", "s", {"config": "e.yaml", "config_dir": None})
        assert calls[-1] == ("shell", "s")


class TestSessionLibrary:
    def test_declares_and_registers(self, local):
        library = Session("android", exporter_config="exporter-android.yaml")
        assert library.name == "android"
        assert getattr(library, context.SESSION_MARKER) == "android"
        assert sessions.names() == ["android"]
        assert library.get_keyword_names() == ["Connect Android Session", "Release Android Session"]
        assert "``android``" in library.get_keyword_documentation("Connect Android Session")
        assert "Close the ``android`` session" in library.get_keyword_documentation("Release Android Session")
        assert library.get_keyword_documentation("__intro__") == Session.__doc__
        assert local == []
        assert library.run_keyword("Connect Android Session", [], {}) == "android-exporter"
        assert local == [("android", "exporter-android.yaml")]
        library.run_keyword("Release Android Session", [], {})
        assert sessions.opened("android") is None
        library.close()
        assert sessions.names() == []

    def test_connect_failure_is_a_test_failure(self):
        from robot.api import Failure

        library = Session("nowhere")
        with pytest.raises(Failure, match="Connect Nowhere Session: session 'nowhere' has nothing to connect to"):
            library.run_keyword("Connect Nowhere Session", [], {})


CLIENT_RESOURCE = """
*** Settings ***
Library    jumpstarter_robot.Jumpstarter
Library    jumpstarter_robot.Session    {name}    exporter_config=${{CURDIR}}/exporter-{name}.yaml
...    AS    session.{name}
Library    jumpstarter_robot.Driver    device.power    client={power}    prefix=Device Power    AS    device.power
"""


class TestSessionsPerSuite:
    def test_each_suite_uses_its_own_session(self, tmp_path, local, trees, run_robot):
        for name in ("android", "ios"):
            write(tmp_path, f"client-{name}.resource", CLIENT_RESOURCE.format(name=name, power=POWER))
        write(
            tmp_path,
            "tests/android.robot",
            """
            *** Settings ***
            Resource    ../client-android.resource

            *** Test Cases ***
            Android
                ${exporter}=    Connect Android Session
                Should Be Equal    ${exporter}    android-exporter
                Device Power On
                ${name}=    Get Exporter Name
                Should Be Equal    ${name}    android-exporter
                ${status}=    Call Driver    device.power    status
                Should Be Equal    ${status}    on
                Release Android Session
                ${status}=    Device Power Status
                Should Be Equal    ${status}    on
            """,
        )
        write(
            tmp_path,
            "tests/ios.robot",
            """
            *** Settings ***
            Resource    ../client-ios.resource

            *** Test Cases ***
            Ios
                ${name}=    Get Exporter Name
                Should Be Equal    ${name}    ios-exporter
                ${status}=    Device Power Status
                Should Be Equal    ${status}    off
                Device Power On
                ${name}=    Get Exporter Name    session=android
                Should Be Equal    ${name}    android-exporter
            """,
        )
        run_robot(tmp_path / "tests").assert_passed()
        android_config = str(tmp_path / "exporter-android.yaml")
        assert local == [
            ("android", android_config),
            ("android", android_config),  # reopened after Release Android Session
            ("ios", str(tmp_path / "exporter-ios.yaml")),
        ]
        assert trees["android"].children["device"].children["power"].status() == "on"
        assert trees["ios"].children["device"].children["power"].status() == "on"
        assert sessions.opened("android") is None and sessions.opened("ios") is None
        assert sessions.names() == []


def test_declarations_of_the_same_target_are_equal(monkeypatch):
    def declaration(**kwargs):
        return session_config("rig", **kwargs).declaration

    lease = declaration(selector="board=b,lab=x")
    assert lease == declaration(selector="lab=x, board=b")
    assert lease == declaration(selector="board=b,lab=x", exporter_config="exporter.yaml")
    assert lease != declaration(selector="board=b")
    assert declaration(selector="a~b") != declaration(selector="a~c")  # unreadable, so compared as text
    assert declaration(lease_name="l1") == declaration(lease_name="l1", selector="board=b")
    assert declaration(exporter_config="a.yaml") != declaration(exporter_config="b.yaml")
    local = session_config("rig", exporter_config="a.yaml").local_target
    assert declaration(exporter_config="a.yaml") == local
    monkeypatch.setenv("JMP_LEASE", "l1")
    assert declaration(selector="board=b") == declaration(lease_name="l1")
    # Start Local Exporter serves the config all the same
    assert session_config("rig", exporter_config="a.yaml").local_target == local
