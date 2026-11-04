from __future__ import annotations

from datetime import timedelta

import pytest
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_power.driver import MockPower

from . import context, sessions
from .conftest import write
from .errors import JumpstarterRobotError
from .session_library import Session, SessionConfig, duration_of, session_config, text
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
    def test_text(self):
        assert text(None) is None
        assert text("") is None
        assert text("  ") is None
        assert text(" a ") == "a"
        assert text(3) == "3"

    def test_duration_of(self):
        assert duration_of("1 minute 30 s") == timedelta(seconds=90)
        assert duration_of("") is None
        assert duration_of(None) is None
        assert duration_of(5) == timedelta(seconds=5)

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

    def test_exporter_class_is_refused(self):
        with pytest.raises(JumpstarterRobotError, match="ExporterClass"):
            session_config("x", exporter_class="rpi4-class")


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

    def test_open_lease_overrides(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(sessions, "open_lease", lambda name, **kw: seen.update(kw, name=name))
        config = SessionConfig(name="lab", selector="a=b", config="lab-eu")
        config.open_lease(selector="c=d", lease_name=None, duration=timedelta(minutes=1))
        assert seen["name"] == "lab"
        assert seen["selector"] == "c=d"  # overrides win
        assert seen["config"] == "lab-eu"  # None overrides keep the configured value
        assert seen["lease_name"] is None
        assert seen["duration"] == timedelta(minutes=1)

    def test_open_lease_passes_reuse(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(sessions, "open_lease", lambda name, **kw: seen.update(kw))
        SessionConfig(name="lab", selector="a=b").open_lease()
        assert seen["reuse_lease"] is True
        session_config("lab", selector="a=b", reuse_lease=False).open_lease()
        assert seen["reuse_lease"] is False

    def test_wants_lease(self):
        assert SessionConfig(exporter_name="rpi4-01").wants_lease
        assert SessionConfig(lease_name="l").wants_lease
        assert not SessionConfig(exporter_config="x").wants_lease


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

    def test_registers_its_config(self):
        library = Session("lab", selector="board=rpi4", reuse_lease=False)
        assert sessions.registered_config("lab") is library.config
        assert library.config.reuse_lease is False
        library.close()
        assert sessions.registered_config("lab") is None

    def test_lease_end_policy_per_session(self):
        Session("lab", selector="board=rpi4", on_lease_end="fail")
        Session("bench", selector="board=rpi5")
        assert sessions.lease_end_policy("lab") == "fail"
        assert sessions.lease_end_policy("bench") == "fatal"
        assert sessions.lease_end_policy() == "fatal"
        with pytest.raises(ValueError, match="on_lease_end"):
            Session("x", on_lease_end="explode")

    def test_keywords_take_no_arguments(self):
        from robot.libdoc import LibraryDocumentation

        doc = LibraryDocumentation("jumpstarter_robot.Session::lab::selector=board=rpi4")
        assert {kw.name: list(kw.args) for kw in doc.keywords} == {
            "Connect Lab Session": [],
            "Release Lab Session": [],
        }
        assert sessions.opened("lab") is None

    def test_multi_word_names(self):
        library = Session("can_sim", selector="role=can-sim")
        assert library.get_keyword_names() == ["Connect Can Sim Session", "Release Can Sim Session"]

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

    def test_two_sessions_in_one_suite_must_be_named(self, tmp_path, local, run_robot):
        suite = write(
            tmp_path,
            "both.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter
            Library    jumpstarter_robot.Session    android    exporter_config=a.yaml    AS    session.android
            Library    jumpstarter_robot.Session    ios    exporter_config=i.yaml    AS    session.ios

            *** Test Cases ***
            Explicit Sessions
                ${a}=    Get Exporter Name    session=android
                ${i}=    Get Exporter Name    session=ios
                Should Be Equal    ${a}-${i}    android-exporter-ios-exporter
                Run Keyword And Expect Error    *no Jumpstarter session 'default'*    Get Exporter Name
                Switch Session    ios
                ${name}=    Get Exporter Name
                Should Be Equal    ${name}    ios-exporter
            """,
        )
        run_robot(suite).assert_passed()


LEASE_CONTROL = """
from jumpstarter_robot import sessions


def end_lease(session):
    sessions.opened(session).lease.lease_ended = True
"""


class TestSessionLeaseEnd:
    def test_session_policy_fails_only_its_keywords(self, tmp_path, trees, run_robot):
        def lease(name, **kwargs):
            return sessions.Session(
                name=name, kind="lease", client=trees["android"], lease=FakeLease(), lease_name=f"lease-{name}"
            )

        write(tmp_path, "leasecontrol.py", LEASE_CONTROL)
        suite = write(
            tmp_path,
            "policy.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    auto_import=False
            Library    jumpstarter_robot.Session    lab    selector=board=rpi4    on_lease_end=fail    AS    session.lab
            Library    ${CURDIR}/leasecontrol.py

            *** Test Cases ***
            Lease Ends
                Connect Lab Session
                End Lease    lab
                Run Keyword And Expect Error    *lease lease-lab has ended*    Lease Should Be Active

            Next Test Runs
                Release Lab Session
            """,
        )
        with use_session_factory(lease=lease):
            run_robot(suite).assert_passed()


class FakeLease:
    lease_ended = lease_transferred = lease_revoked = False


class TestSwitchSessionScope:
    def test_switch_ends_with_its_suite(self, tmp_path, run_robot):
        with serve(MockPower()) as power:

            def local(name, **kwargs):
                return sessions.Session.from_client(power, name, exporter=f"exp-{name}")

            write(
                tmp_path,
                "suites/a1.robot",
                """
                *** Settings ***
                Library    jumpstarter_robot.Jumpstarter    auto_import=False
                Library    jumpstarter_robot.Session    bench    exporter_config=bench.yaml    AS    session.bench
                Library    jumpstarter_robot.Session    pair    exporter_config=pair.yaml    AS    session.pair

                *** Test Cases ***
                Uses Pair
                    Switch Session    pair
                    ${name}=    Get Exporter Name
                    Should Be Equal    ${name}    exp-pair

                Switch Lasts For The Suite
                    ${name}=    Get Exporter Name
                    Should Be Equal    ${name}    exp-pair
                """,
            )
            write(
                tmp_path,
                "suites/a2.robot",
                """
                *** Settings ***
                Library    jumpstarter_robot.Jumpstarter    auto_import=False
                Library    jumpstarter_robot.Session    bench    exporter_config=bench.yaml    AS    session.bench

                *** Test Cases ***
                Only Bench Declared
                    ${name}=    Get Exporter Name
                    Should Be Equal    ${name}    exp-bench
                """,
            )
            with use_session_factory(local=local):
                run_robot(tmp_path / "suites").assert_passed()
        assert sessions.default_switched() is False


class TestCommandLineFallback:
    """``-v JMP_SELECTOR`` reaches a named session; a resource's own value never leaks into it."""

    SUITE = """\
    *** Settings ***
    Library    jumpstarter_robot.Session    default    exporter_config=${CURDIR}/x.yaml    selector=board=default-own
    Library    jumpstarter_robot.Session    named    exporter_config=${CURDIR}/x.yaml    AS    session.named

    *** Test Cases ***
    Configs
        ${named}=    Evaluate    jumpstarter_robot.sessions.registered_config("named").selector
        ...    modules=jumpstarter_robot
        Set Suite Metadata    named    ${named}
    """

    def test_named_session_takes_the_command_line_selector(self, tmp_path, run_robot):
        suite = write(tmp_path, "s.robot", self.SUITE)
        result = run_robot(suite, variable=["JMP_SELECTOR:board=cli"])
        assert result.rc == 0, result.tests
        assert result.result.suite.metadata["named"] == "board=cli"

    def test_default_session_value_does_not_reach_the_named_one(self, tmp_path, run_robot):
        suite = write(tmp_path, "s.robot", self.SUITE)
        result = run_robot(suite)
        assert result.rc == 0, result.tests
        assert result.result.suite.metadata["named"] == "None"
