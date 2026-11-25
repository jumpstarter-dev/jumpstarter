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
from .naming import session_variable
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

    def test_acquire_lease_reports_an_ended_lease(self, leases):
        library = Jumpstarter(selector="board=rpi4", auto_import=False)
        library.acquire_lease()
        assert library.acquire_lease() == "lease-1"  # live: nothing to do
        opened = sessions.opened()
        assert opened is not None
        opened.lease.lease_ended = True
        with pytest.raises(Exception, match="has ended"):
            library.acquire_lease()
        library.release_lease()
        assert library.acquire_lease() == "lease-2"

    def test_close_closes_every_owned_session(self, leases):
        library = Jumpstarter(selector="board=rpi4", auto_import=False)
        library.acquire_lease()
        library.acquire_lease(session="another")
        for name in ("default", "another"):
            opened = sessions.opened(name)
            assert opened is not None
            opened._stack.callback(lambda: 1 // 0)
        with pytest.raises(ExceptionGroup):
            library.close()
        assert sessions.opened("default") is None and sessions.opened("another") is None

    def test_failed_driver_imports_are_retried(self, tmp_path, leases, run_robot, monkeypatch):
        real = Jumpstarter._import_drivers
        attempts = []

        def flaky(self, session):
            attempts.append(session.name)
            if len(attempts) == 1:
                raise RuntimeError("broken driver package")
            real(self, session)

        monkeypatch.setattr(Jumpstarter, "_import_drivers", flaky)
        suite = write(
            tmp_path,
            "retry.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter

            *** Test Cases ***
            Retry
                Run Keyword And Expect Error    *broken driver package*    Acquire Lease    selector=board=rpi4
                Acquire Lease
                Dut Power On
            """,
        )
        run_robot(suite).assert_passed()
        assert len(leases.calls) == 1

    def test_lease_end_policy_lasts_for_one_run(self, tmp_path, leases, run_robot):
        write(tmp_path, "control.py", LEASE_CONTROL)
        body = """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    selector=board=rpi4    auto_import=False{policy}
            Library    ${{CURDIR}}/control.py

            *** Test Cases ***
            Lease Ends
                Acquire Lease
                End Lease
                List Drivers

            Next
                No Operation
            """
        fail = write(tmp_path, "a.robot", body.format(policy="    on_lease_end=fail"))
        default = write(tmp_path, "b.robot", body.format(policy=""))
        assert run_robot(fail).status("Next") == "PASS"
        assert run_robot(default).status("Next") == "FAIL"  # fatal again: the run stopped

    def test_roles_of_another_suite_are_left_alone(self, tmp_path, served, run_robot):
        role = f"Library    jumpstarter_robot.Driver    dut.power    role=power    client={POWER}    AS    power"
        write(
            tmp_path,
            "suites/1.robot",
            f"""
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    auto_import=False
            {role}

            *** Test Cases ***
            Has The Role
                ${{bound}}=    Use Device    dut2
                Should Be Equal    ${{bound}}    ${{{{['dut2.power']}}}}
                ${{names}}=    List Driver Keywords    dut.power
                Should Contain    ${{names}}    Dut Power On
            """,
        )
        write(
            tmp_path,
            "suites/2.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    auto_import=False

            *** Test Cases ***
            Has No Role
                Run Keyword And Expect Error    *no role library matches*    Use Device    dut2
            """,
        )
        with use_client(served):
            run_robot(tmp_path / "suites").assert_passed()

    def test_a_sibling_suite_gets_the_exporter_it_declares(self, tmp_path, leases, run_robot):
        for index, board in enumerate(("rpi4", "rpi5"), 1):
            write(
                tmp_path,
                f"suites/{index}.robot",
                f"""
                *** Settings ***
                Library    jumpstarter_robot.Jumpstarter    auto_import=False
                Library    jumpstarter_robot.Session    rig    selector=board={board}    AS    session.rig
                Suite Setup    Acquire Lease    session=rig

                *** Test Cases ***
                Drive {board}
                    ${{lease}}=    Get Lease Name    session=rig
                    Should Be Equal    ${{lease}}    lease-{index}
                """,
            )
        run_robot(tmp_path / "suites").assert_passed()
        assert [call[1]["selector"] for call in leases.calls] == ["board=rpi4", "board=rpi5"]

    def test_child_suites_keep_an_override_of_their_parent(self, tmp_path, leases, run_robot):
        write(
            tmp_path,
            "client.resource",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    auto_import=False
            Library    jumpstarter_robot.Session    rig    selector=board=rpi4    AS    session.rig
            """,
        )
        write(
            tmp_path,
            "suites/__init__.robot",
            """
            *** Settings ***
            Resource    ../client.resource
            Suite Setup    Acquire Lease    selector=board=special    session=rig
            """,
        )
        for index in (1, 2):
            write(
                tmp_path,
                f"suites/{index}.robot",
                f"""
                *** Settings ***
                Resource    ../client.resource

                *** Test Cases ***
                Child {index}
                    ${{lease}}=    Get Lease Name
                    Should Be Equal    ${{lease}}    lease-1
                """,
            )
        run_robot(tmp_path / "suites").assert_passed()
        assert len(leases.calls) == 1

    def test_indistinguishable_session_names_are_refused(self, tmp_path, leases, run_robot):
        for name in ("board-a", "board_a"):
            variable = session_variable("JMP_SELECTOR", name)
            write(
                tmp_path,
                f"{name}.resource",
                f"""
                *** Settings ***
                Library    jumpstarter_robot.Jumpstarter    auto_import=False
                Library    jumpstarter_robot.Session    {name}    selector=${{{variable}}}    AS    session.{name}

                *** Variables ***
                ${{{variable}}}    board={name}
                """,
            )
        suite = write(
            tmp_path,
            "both.robot",
            """
            *** Settings ***
            Resource    board-a.resource
            Resource    board_a.resource

            *** Test Cases ***
            First
                Get Exporter Name    session=board-a

            Second
                Get Exporter Name    session=board_a
            """,
        )
        run = run_robot(suite)
        assert run.status("First") == "PASS"
        assert run.status("Second") == "FAIL"  # rather than driving board-a
        assert any("same variable and keyword names" in error.message for error in run.result.errors)
        assert [name for name, _ in leases.calls] == ["board-a"]

    def test_a_sibling_suite_reconnects_and_imports_drivers(self, tmp_path, leases, run_robot):
        head = """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    selector=board=b

            *** Test Cases ***
            """
        write(
            tmp_path,
            "suites/1.robot",
            head
            + """
            Override
                Release Lease
                Acquire Lease    selector=lab=x
                Dut Power On
            """,
        )
        write(
            tmp_path,
            "suites/2.robot",
            head
            + """
            Declared
                Dut Power On
                ${lease}=    Get Lease Name
                Should Be Equal    ${lease}    lease-3
            """,
        )
        run_robot(tmp_path / "suites").assert_passed()
        assert [call[1]["selector"] for call in leases.calls] == ["board=b", "lab=x", "board=b"]

    def test_a_role_imported_twice_is_bound_once(self, tmp_path, served, run_robot):
        role = f"Library    jumpstarter_robot.Driver    dut.power    role=power    client={POWER}"
        suite = write(
            tmp_path,
            "twice.robot",
            f"""
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    auto_import=False
            {role}    AS    power
            {role}    AS    psu

            *** Test Cases ***
            Bound Once
                ${{bound}}=    Use Device    dut2
                Should Be Equal    ${{bound}}    ${{{{['dut2.power']}}}}
            """,
        )
        with use_client(served):
            run_robot(suite).assert_passed()

    @pytest.mark.parametrize("other", ["board=b", None])
    def test_a_reused_import_reconnects_after_a_release(self, tmp_path, leases, run_robot, other):
        head = """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    selector={selector}
            Suite Teardown    Release Lease

            *** Test Cases ***
            Drive {selector}
                Dut Power On
            """
        for index, selector in enumerate(filter(None, ["board=a", other, "board=a"]), 1):
            write(tmp_path, f"suites/{index}.robot", head.format(selector=selector))
        run_robot(tmp_path / "suites").assert_passed()

    def test_a_local_exporter_is_replaced_by_the_lease_a_suite_declares(
        self, tmp_path, leases, served, run_robot, monkeypatch
    ):
        monkeypatch.setenv("JMP_LEASE", "mine")

        def local(name, *, config, config_dir=None):
            return sessions.Session.from_client(served, name, exporter=f"local-{config}")

        head = """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    auto_import=False
            Library    jumpstarter_robot.Session    rig    selector=board=a    AS    session.rig

            *** Test Cases ***
            """
        write(
            tmp_path,
            "suites/1.robot",
            head
            + """
            Simulated
                Start Local Exporter    sim.yaml    session=rig
            """,
        )
        write(
            tmp_path,
            "suites/2.robot",
            head
            + """
            Leased
                ${exporter}=    Get Exporter Name    session=rig
                Should Be Equal    ${exporter}    exporter-rig
            """,
        )
        with use_session_factory(local=local):
            run_robot(tmp_path / "suites").assert_passed()

    @pytest.mark.parametrize("order", [("board-a", "board_a"), ("board_a", "board-a")])
    @pytest.mark.parametrize("literal", [False, True], ids=["variables", "literals"])
    def test_reused_sessions_with_clashing_names_keep_their_exporters(
        self, tmp_path, leases, run_robot, order, literal
    ):
        """Robot Framework reuses a library only for the same arguments, so a shadowed variable is a new import."""
        for name in order:
            variable = session_variable("JMP_SELECTOR", name)
            selector = f"board={name}" if literal else f"${{{variable}}}"
            write(
                tmp_path,
                f"{name}.resource",
                f"""
                *** Settings ***
                Library    jumpstarter_robot.Jumpstarter    auto_import=False
                Library    jumpstarter_robot.Session    {name}    selector={selector}    AS    session.{name}

                *** Variables ***
                ${{{variable}}}    board={name}
                """,
            )
        for index, names in enumerate(((order[0],), (order[1],), order), 1):
            resources = "\n".join(f"Resource    ../{name}.resource" for name in names)
            cases = "\n".join(
                f"Open {name} In {index}\n"
                f"    Release Lease    session={name}\n"
                f"    ${{exporter}}=    Get Exporter Name    session={name}\n"
                f"    Should Be Equal    ${{exporter}}    exporter-{name}\n"
                for name in names
            )
            write(
                tmp_path, f"suites/{index}.robot", "*** Settings ***\n" + resources + "\n*** Test Cases ***\n" + cases
            )
        run = run_robot(tmp_path / "suites")
        assert leases.calls
        # whatever was refused, no session was ever opened for the other one's selector
        assert {(name, call["selector"]) for name, call in leases.calls} == {(name, f"board={name}") for name in order}
        refused = any("same variable and keyword names" in error.message for error in run.result.errors)
        assert refused is not literal
