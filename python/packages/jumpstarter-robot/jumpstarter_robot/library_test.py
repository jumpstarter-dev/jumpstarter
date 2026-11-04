from __future__ import annotations

from dataclasses import dataclass

import pytest
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_power.driver import MockPower
from jumpstarter_driver_shell.driver import Shell
from robot.api import FatalError

from . import sessions
from .conftest import write
from .errors import JumpstarterRobotError
from .keywords import load_all
from .library import Jumpstarter, walk_tree
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

    def test_missing_keyword_set_modules_are_skipped(self, monkeypatch):
        from . import keywords

        monkeypatch.setitem(keywords.KEYWORD_SETS, "not_written_yet", "NotWrittenYetKeywords")
        classes = load_all()
        assert "NotWrittenYetKeywords" not in [cls.__name__ for cls in classes]
        assert "acquire_lease" in Jumpstarter().get_keyword_names()

    def test_broken_keyword_set_dependency_is_reported(self, monkeypatch):
        from . import keywords

        def import_module(name):
            # a keyword set must import optional dependencies lazily; one that does not is a bug to surface
            raise ModuleNotFoundError("No module named 'adbutils'", name="adbutils")

        monkeypatch.setattr(keywords, "KEYWORD_SETS", {"adb": "AdbKeywords"})
        monkeypatch.setattr(keywords.importlib, "import_module", import_module)
        with pytest.raises(ModuleNotFoundError, match="adbutils"):
            load_all()

    def test_set_keywords_dispatch_to_their_owner(self):
        library = Jumpstarter()
        for attr, owner in library._names.items():
            assert getattr(library, attr).__self__ is owner

    def test_unknown_attribute(self):
        with pytest.raises(AttributeError):
            Jumpstarter().not_a_keyword  # noqa: B018

    def test_libdoc_lists_every_keyword(self):
        from robot.libdoc import LibraryDocumentation

        doc = LibraryDocumentation("jumpstarter_robot.Jumpstarter")
        names = {kw.name for kw in doc.keywords}
        assert {"Acquire Lease", "Release Lease", "Call Driver", "Lease Should Be Active", "Use Device"} <= names
        acquire = next(kw for kw in doc.keywords if kw.name == "Acquire Lease")
        assert "board=rpi4" in acquire.doc
        assert str(next(iter(acquire.args))) == "selector: str | None = None"


class TestOutsideRobot:
    def test_no_arguments_declares_nothing(self):
        library = Jumpstarter()
        assert not hasattr(library, "_jumpstarter_session_name")
        assert sessions.names() == []

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

    def test_exporter_class_not_supported(self):
        with pytest.raises(JumpstarterRobotError, match="ExporterClass"):
            Jumpstarter(exporter_class="rpi4")

    def test_bad_lease_end_policy(self):
        with pytest.raises(ValueError, match="on_lease_end"):
            Jumpstarter(on_lease_end="explode")

    def test_import_without_lease_end_policy_keeps_it(self):
        Jumpstarter(on_lease_end="fail")
        Jumpstarter(auto_import=False)  # as a generated client resource imports it
        assert sessions.lease_end_policy() == "fail"

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

    def test_acquire_lease_reuse(self, leases):
        library = Jumpstarter(reuse_lease=False, session="lab", selector="board=rpi4")
        library.acquire_lease(session="lab")
        assert leases.calls[0][1]["reuse_lease"] is False
        library.release_lease("lab")
        Jumpstarter().acquire_lease(selector="board=rpi4", reuse=False)
        assert leases.calls[1][1]["reuse_lease"] is False

    def test_acquire_lease_uses_the_declared_session_config(self, leases):
        from .session_library import Session

        Session("lab", selector="board=rpi4", config="lab-eu", duration="2 hours", release_grace="0", reuse_lease=False)
        Jumpstarter(auto_import=False).acquire_lease(selector="board=rpi5", session="lab")
        name, kwargs = leases.calls[0]
        assert name == "lab"
        assert kwargs["selector"] == "board=rpi5"  # the keyword's own arguments win
        assert kwargs["config"] == "lab-eu"
        assert kwargs["duration"].total_seconds() == 7200
        assert kwargs["release_grace"] == 0
        assert kwargs["reuse_lease"] is False

    def test_lease_should_be_active(self, leases):
        library = Jumpstarter()
        library.acquire_lease(selector="x")
        session = sessions.opened()
        assert session is not None
        session.lease.lease_ended = True
        with pytest.raises(FatalError, match="Lease Should Be Active: lease lease-1 has ended"):
            library.lease_should_be_active()

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

    def test_switch_session(self, served):
        library = Jumpstarter()
        with use_client(served, "a", exporter="A"), use_client(served, "b", exporter="B"):
            assert library.switch_session("b") == "default"
            assert library.get_exporter_name() == "B"
            assert library.get_exporter_name("a") == "A"

    def test_connect_to_shell_session_needs_a_shell(self):
        with pytest.raises(JumpstarterRobotError, match="JUMPSTARTER_HOST"):
            Jumpstarter().connect_to_shell_session()

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

    def test_walk_tree(self, served):
        assert [path for path, _ in walk_tree(served)] == ["dut", "dut.power", "dut2", "dut2.power", "shell"]


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

    def test_second_session_keywords_carry_its_name(self, tmp_path, leases, run_robot):
        suite = write(
            tmp_path,
            "two.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter

            *** Test Cases ***
            Two Leases Side By Side
                Acquire Lease    selector=board=rpi4
                Acquire Lease    selector=role=sim    session=sim
                # both exporters have dut.power: the first session's keywords keep its full path,
                # the second's get the session name in front
                Dut Power On
                Sim Dut Power Off
                ${result}=    Sim Shell Hello    there
                Should Contain    ${result.stdout}    hello there
                ${keywords}=    List Driver Keywords    dut.power    session=sim
                Should Contain    ${keywords}    Sim Dut Power On
                Should Not Contain    ${keywords}    Dut Power On
                ${keywords}=    List Driver Keywords    dut.power
                Should Contain    ${keywords}    Dut Power On
                Release Lease    session=sim
                Release Lease
            """,
        )
        run = run_robot(suite)
        run.assert_passed()
        assert [name for name, _ in leases.calls] == ["default", "sim"]

    def test_library_arguments_lease(self, tmp_path, leases, run_robot):
        suite = write(
            tmp_path,
            "args.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    selector=board=rpi4    session=lab

            *** Test Cases ***
            Declared Session Is Used
                Dut Power On
                ${name}=    Get Exporter Name
                Should Be Equal    ${name}    exporter-lab
                ${status}=    lab.dut.power.Dut Power Status
                Should Be Equal    ${status}    on
            """,
        )
        run_robot(suite).assert_passed()
        assert [name for name, _ in leases.calls] == ["lab"]
        assert sessions.opened("lab") is None  # released when the library closed
        assert leases.calls[0][1]["acquire"] is False  # opening on its own only attaches

    def test_only_acquire_lease_acquires(self, tmp_path, served, run_robot):
        leases = LeaseFactory(served)

        def controller(name="default", **kwargs):
            # no lease exists yet: attaching fails, acquiring is granted
            if kwargs.get("acquire", True) is False:
                raise sessions.NoSession(f"session '{name}': no active lease matches board=rpi4")
            return leases(name, **kwargs)

        suite = write(
            tmp_path,
            "acquire.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    selector=board=rpi4

            *** Test Cases ***
            Acquire In Suite Code
                Acquire Lease
                Dut Power On
            """,
        )
        with use_session_factory(lease=controller):
            run_robot(suite).assert_passed()
        assert len(leases.calls) == 1
        assert leases.calls[0][1]["selector"] == "board=rpi4"
        assert leases.calls[0][1].get("acquire", True) is True

    def test_jmp_lease_attaches_instead_of_the_local_exporter(self, tmp_path, leases, run_robot, monkeypatch):
        monkeypatch.setenv("JMP_LEASE", "made-by-hand")
        write(tmp_path, "exporter.yaml", "unused: the lease wins\n")
        suite = write(
            tmp_path,
            "env.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    exporter_config=${CURDIR}/exporter.yaml

            *** Test Cases ***
            Attached
                Dut Power On
            """,
        )
        run_robot(suite).assert_passed()
        assert leases.calls[0][1]["acquire"] is False

    def test_acquire_lease_serves_a_local_exporter_config(self, tmp_path, served, run_robot):
        opened = []

        def local(name="default", **kwargs):
            opened.append((name, kwargs["config"]))
            return sessions.Session.from_client(served, name, exporter="bench")

        write(tmp_path, "exporter.yaml", "served by the fake\n")
        suite = write(
            tmp_path,
            "local.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    exporter_config=${CURDIR}/exporter.yaml

            *** Test Cases ***
            Same Setup Locally
                ${lease}=    Acquire Lease
                Should Be Equal    ${lease}    ${None}
                ${name}=    Get Exporter Name
                Should Be Equal    ${name}    bench
            """,
        )
        with use_session_factory(local=local):
            run_robot(suite).assert_passed()
        assert opened == [("default", str(tmp_path / "exporter.yaml"))]

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

    def test_lease_end_fails_only_the_keyword(self, tmp_path, leases, run_robot):
        write(tmp_path, "leasecontrol.py", LEASE_CONTROL)
        suite = write(
            tmp_path,
            "fail.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    on_lease_end=fail
            Library    ${CURDIR}/leasecontrol.py

            *** Test Cases ***
            Lease Ends
                Acquire Lease    selector=x
                End Lease
                Run Keyword And Expect Error    *lease lease-1 has ended*    Lease Should Be Active
                Run Keyword And Expect Error    *has ended*    Dut Power On

            Next Test Runs
                Release Lease
            """,
        )
        run_robot(suite).assert_passed()

    def test_lease_end_policy_survives_a_client_resource(self, tmp_path, leases, run_robot):
        write(tmp_path, "leasecontrol.py", LEASE_CONTROL)
        # what every generated client resource imports
        resource = "*** Settings ***\nLibrary    jumpstarter_robot.Jumpstarter    auto_import=False\n"
        write(tmp_path, "client.resource", resource)
        suite = write(
            tmp_path,
            "fail.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    on_lease_end=fail
            Resource    ${CURDIR}/client.resource
            Library    ${CURDIR}/leasecontrol.py

            *** Test Cases ***
            Lease Ends
                Acquire Lease    selector=x
                End Lease
                Run Keyword And Expect Error    *lease lease-1 has ended*    Lease Should Be Active

            Next Test Runs
                Release Lease
            """,
        )
        run_robot(suite).assert_passed()

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

    def test_bind_driver_and_role_reset(self, tmp_path, served, run_robot):
        suite = write(
            tmp_path,
            "roles.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    auto_import=False
            Library    jumpstarter_robot.Driver    role=power    path=dut.power    client=POWER_CLIENT    prefix=Power
            ...    AS    power
            Library    jumpstarter_robot.Driver    shell    client=jumpstarter_driver_shell.client.ShellClient
            ...    AS    shell

            *** Test Cases ***
            Bind To Second Dut
                ${previous}=    Bind Driver    power    dut2.power
                Should Be Equal    ${previous}    dut.power
                Power On
                ${status}=    Call Driver    dut2.power    status
                Should Be Equal    ${status}    on
                Run Keyword And Expect Error    *ShellClient*    Bind Driver    power    shell
                Power Off
                Run Keyword And Expect Error    *not a jumpstarter_robot.Driver*    Bind Driver    BuiltIn    x

            Binding Was Reset
                Power On
                ${status}=    Call Driver    dut.power    status
                Should Be Equal    ${status}    on
                ${status}=    Call Driver    dut2.power    status
                Should Be Equal    ${status}    off

            Use Device Loops
                FOR    ${dut}    IN    dut    dut2
                    ${bound}=    Use Device    ${dut}
                    Should Be Equal    ${bound}[0]    ${dut}.power
                    Power Off
                END
                Run Keyword And Expect Error    *no role library*    Use Device    shell
            """.replace("POWER_CLIENT", POWER),
        )
        with use_client(served):
            run_robot(suite).assert_passed()

    def test_switch_session_in_robot(self, tmp_path, served, run_robot):
        suite = write(
            tmp_path,
            "switch.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    auto_import=False

            *** Test Cases ***
            Switch
                ${previous}=    Switch Session    other
                Should Be Equal    ${previous}    default
                ${name}=    Get Exporter Name
                Should Be Equal    ${name}    other-exporter
                ${name}=    Get Exporter Name    session=default
                Should Be Equal    ${name}    default-exporter
            """,
        )
        with use_client(served, exporter="default-exporter"), use_client(served, "other", exporter="other-exporter"):
            run_robot(suite).assert_passed()
