from __future__ import annotations

from dataclasses import dataclass

import pytest
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_power.driver import MockPower
from jumpstarter_driver_shell.driver import Shell

from . import sessions
from .conftest import write
from .errors import JumpstarterRobotError
from .keywords import load_all
from .library import Jumpstarter
from .testing import use_client, use_session_factory
from jumpstarter.common.utils import serve

POWER = "jumpstarter_driver_power.client.PowerClient"


@pytest.fixture(scope="module")
def served():
    root = Composite(
        children={
            "dut": Composite(children={"power": MockPower()}),
            "dut2": Composite(children={"power": MockPower()}),
            "shell": Shell(methods={"hello": "echo hello $1"}),
        }
    )
    with serve(root) as client:
        yield client


@dataclass
class FakeLease:
    lease_ended: bool = False
    lease_transferred: bool = False
    lease_revoked: bool = False


class LeaseFactory:
    """Stands in for Session.from_lease: hands out the served tree as a leased exporter."""

    def __init__(self, client):
        self.client = client
        self.calls = []

    def __call__(self, name="default", **kwargs):
        self.calls.append((name, kwargs))
        return sessions.Session(
            name=name,
            kind="lease",
            client=self.client,
            exporter=f"exporter-{name}",
            labels={"board": "rpi4", "session": name},
            lease_name=f"lease-{len(self.calls)}",
            owns_lease=True,
            lease=FakeLease(),
        )


@pytest.fixture
def leases(served):
    factory = LeaseFactory(served)
    with use_session_factory(lease=factory):
        yield factory


LEASE_CONTROL = """
from jumpstarter_robot import sessions


def end_lease(session="default"):
    sessions.opened(session).lease.lease_ended = True
"""


class TestKeywordNames:
    def test_static_and_set_keywords_once(self):
        names = Jumpstarter().get_keyword_names()
        assert len(names) == len(set(names))
        for expected in ("acquire_lease", "release_lease", "call_driver", "list_drivers", "switch_session"):
            assert expected in names
        for cls in load_all():
            for attr in dir(cls):
                member = getattr(cls, attr)
                if not attr.startswith("_") and callable(member) and hasattr(member, "robot_name"):
                    assert attr in names, f"{cls.__name__}.{attr}"


    def test_libdoc_lists_every_keyword(self):
        from robot.libdoc import LibraryDocumentation

        doc = LibraryDocumentation("jumpstarter_robot.Jumpstarter")
        names = {kw.name for kw in doc.keywords}
        assert {"Acquire Lease", "Release Lease", "Call Driver", "Lease Should Be Active", "Use Device"} <= names
        acquire = next(kw for kw in doc.keywords if kw.name == "Acquire Lease")
        assert "board=rpi4" in acquire.doc
        assert str(next(iter(acquire.args))) == "selector: str | None = None"


class TestOutsideRobot:

    def test_declares_and_registers_lazily(self, leases):
        library = Jumpstarter(selector="board=rpi4", session="lab")
        assert library._jumpstarter_session_name == "lab"
        assert sessions.names() == ["lab"]
        assert leases.calls == []
        assert library.get_exporter_name("lab") == "exporter-lab"
        assert leases.calls[0][0] == "lab"
        assert leases.calls[0][1]["selector"] == "board=rpi4"
        library.close()
        assert sessions.opened("lab") is None
        assert sessions.names() == []


    def test_session_keywords(self, served, leases):
        library = Jumpstarter()
        assert library.acquire_lease(selector="board=rpi4", duration="5 min") == "lease-1"
        assert leases.calls[0][1]["duration"].total_seconds() == 300
        assert leases.calls[0][1]["reuse_lease"] is True
        with pytest.raises(JumpstarterRobotError, match="already connected"):
            library.acquire_lease(selector="board=rpi4")
        assert library.get_lease_name() == "lease-1"
        assert library.get_exporter_name() == "exporter-default"
        assert library.get_exporter_labels()["board"] == "rpi4"
        library.lease_should_be_active()
        library.release_lease()
        library.release_lease()  # idempotent
        assert sessions.opened() is None


    def test_call_driver_and_lists(self, served):
        library = Jumpstarter()
        with use_client(served):
            assert library.call_driver("dut.power", "on") is None
            assert library.call_driver("dut.power", "status") == "on"
            library.call_driver("dut.power", "cycle", wait=0)
            assert library.get_driver("dut.power") is served.children["dut"].children["power"]
            drivers = library.list_drivers()
            assert [d["path"] for d in drivers] == ["dut", "dut.power", "dut2", "dut2.power", "shell"]
            assert drivers[1].client == POWER  # DotDict: attribute access works too
            assert "Dut Power On" in library.list_driver_keywords("dut.power")
            with pytest.raises(Exception, match="driver 'nope'"):
                library.call_driver("nope", "on")


    def test_start_and_stop_local_exporter(self, served):
        opened = []

        def local(name, config, config_dir=None):
            opened.append((name, config))
            return sessions.Session.from_client(served, name, exporter="local-mock")

        library = Jumpstarter()
        with use_session_factory(local=local):
            assert library.start_local_exporter("exporter.yaml", session="rig") == "local-mock"
            with pytest.raises(JumpstarterRobotError, match="already connected"):
                library.start_local_exporter("exporter.yaml", session="rig")
            library.stop_local_exporter("rig")
            assert sessions.opened("rig") is None
        assert opened == [("rig", "exporter.yaml")]


class TestInRobot:
    def test_acquire_lease_auto_imports_drivers(self, tmp_path, leases, run_robot):
        suite = write(
            tmp_path,
            "lease.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter

            *** Test Cases ***
            Acquire And Drive
                ${lease}=    Acquire Lease    selector=board=rpi4    duration=10 minutes
                Should Be Equal    ${lease}    lease-1
                Dut Power On
                ${status}=    Dut Power Status
                Should Be Equal    ${status}    on
                dut2.power.Dut2 Power Off
                ${result}=    Shell Hello    there
                Should Contain    ${result.stdout}    hello there
                ${name}=    Get Exporter Name
                Should Be Equal    ${name}    exporter-default
                ${labels}=    Get Exporter Labels
                Should Be Equal    ${labels.board}    rpi4
                ${status}=    Call Driver    dut.power    status
                Should Be Equal    ${status}    on
                ${drivers}=    List Drivers
                Length Should Be    ${drivers}    5
                Lease Should Be Active

            Still Leased In The Next Test
                Dut Power Off
                Release Lease
                Release Lease
                Run Keyword And Expect Error    *no Jumpstarter session*    Dut Power On
            """,
        )
        run = run_robot(suite)
        run.assert_passed()
        assert len(leases.calls) == 1
        assert leases.calls[0][1]["selector"] == "board=rpi4"
        assert leases.calls[0][1]["duration"].total_seconds() == 600


    def test_lease_end_is_fatal(self, tmp_path, leases, run_robot):
        write(tmp_path, "leasecontrol.py", LEASE_CONTROL)
        suite = write(
            tmp_path,
            "fatal.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter
            Library    ${CURDIR}/leasecontrol.py

            *** Test Cases ***
            Lease Ends
                Acquire Lease    selector=x
                End Lease
                Run Keyword And Expect Error    *    Lease Should Be Active

            Never Runs
                Log    unreachable
            """,
        )
        run = run_robot(suite)
        assert run.status("Lease Ends") == "FAIL"
        assert "has ended" in run.message("Lease Ends")
        assert run.status("Never Runs") == "FAIL"
        assert "fatal error" in run.message("Never Runs")


    def test_dry_run_never_connects(self, tmp_path, leases, run_robot):
        suite = write(
            tmp_path,
            "dry.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    selector=board=rpi4
            Library    jumpstarter_robot.Driver    dut.power    AS    power

            *** Test Cases ***
            Checked Only
                Acquire Lease    selector=board=rpi5    session=other
                Log    hi
            """,
        )
        run = run_robot(suite, dryrun=True)
        assert run.status("Checked Only") == "PASS"
        assert leases.calls == []
        assert sessions.opened() is None
