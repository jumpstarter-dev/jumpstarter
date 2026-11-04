"""End-to-end: plain ``robot.run`` against exporters served in this process."""

from __future__ import annotations

import pytest
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_power.driver import MockPower
from jumpstarter_driver_shell.driver import Shell

from . import sessions
from .conftest import write
from .testing import use_client, use_session_factory
from jumpstarter.common import ExporterStatus
from jumpstarter.common.utils import serve
from jumpstarter.config.env import JMP_DRIVERS_ALLOW, JMP_EXPORTER, JUMPSTARTER_HOST
from jumpstarter.exporter import Session as ExporterSession

POWER = "jumpstarter_driver_power.client.PowerClient"
SHELL = "jumpstarter_driver_shell.client.ShellClient"

EXPORTER_YAML = """
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: e2e-rig
endpoint: ""
token: ""
export:
  dut:
    type: jumpstarter_driver_composite.driver.Composite
    children:
      power:
        type: jumpstarter_driver_power.driver.MockPower
  shell:
    type: jumpstarter_driver_shell.driver.Shell
    config:
      methods:
        hello: "echo hello $1 $GREETING"
"""

CLIENT_RESOURCE = """
*** Settings ***
Library    jumpstarter_robot.Jumpstarter
Library    jumpstarter_robot.Session    rig    exporter_config=${JMP_EXPORTER_CONFIG}    AS    session.rig
Library    jumpstarter_robot.Driver    dut    client=jumpstarter_driver_composite.client.CompositeClient
...    prefix=Dut    session=rig    AS    dut
Library    jumpstarter_robot.Driver    dut.power    client=jumpstarter_driver_power.client.PowerClient
...    prefix=Dut Power    session=rig    AS    dut.power
Library    jumpstarter_robot.Driver    shell    client=jumpstarter_driver_shell.client.ShellClient
...    prefix=Shell    session=rig    methods=hello    AS    shell

*** Variables ***
${JMP_EXPORTER_CONFIG}    ${CURDIR}/exporter.yaml
"""

STATIC_TESTS = """
*** Settings ***
Resource    ../client.resource

*** Test Cases ***
Static Keywords
    Dut Power On
    ${status}=    Dut Power Status
    Should Be Equal    ${status}    on
    dut.power.Dut Power Off
    ${status}=    dut.power.Dut Power Status
    Should Be Equal    ${status}    off
    ${result}=    Shell Hello    there    GREETING=hi
    Should Be Equal    ${result.stdout.strip()}    hello there hi
    ${readings}=    Dut Power Read    max_items=1
    Length Should Be    ${readings}    1
    Should Be True    $readings[0].voltage == 0
"""

ZERO_CONFIG_TESTS = """
*** Settings ***
Library    jumpstarter_robot.Jumpstarter    exporter_config=${CURDIR}/../exporter.yaml

*** Test Cases ***
Zero Config Keywords
    ${drivers}=    List Drivers
    Length Should Be    ${drivers}    3
    Should Be Equal    ${drivers[1].path}    dut.power
    # auto-imported keywords carry the driver's full path, like the generated resource's
    Dut Power On
    Dut Power Should Be On
    ${readings}=    Dut Power Read    max_items=2
    Length Should Be    ${readings}    2
    ${result}=    Shell Hello    world    GREETING=hey
    Should Contain    ${result.stdout}    hello world hey
    Call Driver    dut.power    off
    ${status}=    dut.power.Dut Power Status
    Should Be Equal    ${status}    off
    ${name}=    Get Exporter Name
    Should Be Equal    ${name}    e2e-rig
"""


@pytest.fixture
def project(tmp_path):
    write(tmp_path, "exporter.yaml", EXPORTER_YAML)
    write(tmp_path, "client.resource", CLIENT_RESOURCE)
    return tmp_path


class TestLocalMode:
    def test_zero_config(self, project, run_robot):
        suite = write(project, "tests/zero.robot", ZERO_CONFIG_TESTS)
        run_robot(suite).assert_passed()
        assert sessions.opened() is None  # the local exporter stopped with the run

    def test_zero_config_in_several_suites(self, project, run_robot):
        write(project, "tests/a_zero.robot", ZERO_CONFIG_TESTS)
        write(project, "tests/b_zero.robot", ZERO_CONFIG_TESTS)
        run = run_robot(project / "tests")
        run.assert_passed()
        assert run.result.statistics.total.passed == 2

    def test_import_library_at_run_time(self, project, run_robot):
        suite = write(
            project,
            "tests/runtime.robot",
            """
            *** Test Cases ***
            Import In The Test
                Import Library    jumpstarter_robot.Jumpstarter    exporter_config=${CURDIR}/../exporter.yaml
                Dut Power On
                Dut Power Should Be On
            """,
        )
        run_robot(suite).assert_passed()

    def test_client_resource(self, project, run_robot):
        suite = write(project, "tests/static.robot", STATIC_TESTS)
        run_robot(suite).assert_passed()
        assert sessions.opened("rig") is None

    def test_client_resource_and_zero_config_together(self, project, run_robot):
        write(project, "tests/a_static.robot", STATIC_TESTS)
        write(project, "tests/b_zero.robot", ZERO_CONFIG_TESTS)
        run_robot(project / "tests").assert_passed()

    def test_variable_override(self, project, run_robot, tmp_path):
        other = tmp_path / "other"
        write(other, "exporter.yaml", EXPORTER_YAML.replace("e2e-rig", "other-rig"))
        suite = write(
            project,
            "tests/override.robot",
            """
            *** Settings ***
            Resource    ../client.resource

            *** Test Cases ***
            Uses The Overridden Config
                ${name}=    Connect Rig Session
                Should Be Equal    ${name}    other-rig
            """,
        )
        run_robot(suite, variable=[f"JMP_EXPORTER_CONFIG:{other / 'exporter.yaml'}"]).assert_passed()


class TestDryRun:
    """``robot --dryrun`` checks suites without connecting: no lease, no local exporter."""

    @pytest.fixture
    def connects(self):
        attempts = []

        def refuse(*args, **kwargs):
            attempts.append(args)
            raise AssertionError("a dry run connected")

        with use_session_factory(lease=refuse, local=refuse, shell=refuse):
            yield attempts

    def test_client_resource(self, project, connects, run_robot):
        suite = write(project, "tests/static.robot", STATIC_TESTS)
        run_robot(suite, dryrun=True).assert_passed()
        assert connects == []

    def test_zero_config(self, project, connects, run_robot):
        suite = write(project, "tests/zero.robot", ZERO_CONFIG_TESTS)
        run = run_robot(suite, dryrun=True)
        assert connects == []
        # its driver keywords only exist once connected, so the dry run cannot find them
        assert "No keyword with name 'Dut Power On' found" in run.message("Zero Config Keywords")


class TestLeaseInInit:
    def test_suite_setup_lease_reaches_child_suites(self, project, run_robot):
        write(
            project,
            "remote/__init__.robot",
            """
            *** Settings ***
            Library           jumpstarter_robot.Jumpstarter
            Suite Setup       Acquire Lease    selector=board=rpi4
            Suite Teardown    Release Lease
            """,
        )
        write(
            project,
            "remote/a_static.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Driver    dut.power    client=jumpstarter_driver_power.client.PowerClient
            ...    AS    dut.power

            *** Test Cases ***
            Static Library Binds To The Lease
                Dut Power On
                ${status}=    Dut Power Status
                Should Be Equal    ${status}    on
            """,
        )
        write(
            project,
            "remote/b_zero.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter

            *** Test Cases ***
            Auto Imported Keywords Reach The Child Suite
                Dut Power Off
                ${lease}=    Get Lease Name
                Should Be Equal    ${lease}    lease-1
            """,
        )
        leased = []
        with serve(Composite(children={"dut": Composite(children={"power": MockPower()})})) as client:

            def lease(name, **kwargs):
                leased.append(kwargs["selector"])
                return sessions.Session(name=name, kind="lease", client=client, exporter="x", lease_name="lease-1")

            with use_session_factory(lease=lease):
                run_robot(project / "remote").assert_passed()
        assert leased == ["board=rpi4"]


@pytest.fixture
def shell(monkeypatch):
    """A `jmp shell`: an exporter served on a socket named by JUMPSTARTER_HOST."""
    root = Composite(
        children={
            "dut": Composite(children={"power": MockPower()}),
            "shell": Shell(methods={"hello": "echo hello $1 $GREETING"}),
        }
    )
    with ExporterSession(root_device=root) as session, session.serve_unix() as path:
        session.update_status(ExporterStatus.LEASE_READY)
        monkeypatch.setenv(JUMPSTARTER_HOST, str(path))
        monkeypatch.setenv(JMP_DRIVERS_ALLOW, "UNSAFE")
        monkeypatch.setenv(JMP_EXPORTER, "shell-exporter")
        yield path


class TestShell:
    def test_zero_config_uses_the_shell(self, project, shell, run_robot):
        suite = write(
            project,
            "tests/shell.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter

            *** Test Cases ***
            Shell Session
                Dut Power On
                ${name}=    Get Exporter Name
                Should Be Equal    ${name}    shell-exporter
                ${exporter}=    Connect To Shell Session
                Should Be Equal    ${exporter}    shell-exporter
                ${result}=    Shell Hello    from-shell
                Should Contain    ${result.stdout}    hello from-shell
            """,
        )
        run_robot(suite).assert_passed()

    def test_client_resource_under_the_shell(self, project, shell, run_robot):
        # the resource's local exporter config is ignored inside `jmp shell`, and
        # its own Driver imports (with their prefixes) are the ones that count
        write(project, "exporter.yaml", "not: a valid exporter config\n")
        suite = write(project, "tests/static.robot", STATIC_TESTS)
        run_robot(suite).assert_passed()

    def test_acquire_lease_uses_the_shell(self, project, shell, run_robot):
        suite = write(
            project,
            "tests/acquire.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    auto_import=False

            *** Test Cases ***
            Acquire Inside A Shell
                Acquire Lease    selector=ignored=true    session=dut
                ${name}=    Get Exporter Name    session=dut
                Should Be Equal    ${name}    shell-exporter
                ${status}=    Call Driver    dut.power    status    session=dut
                Release Lease    session=dut
            """,
        )

        def no_lease(*args, **kwargs):
            raise AssertionError("a lease was requested inside the shell")

        with use_session_factory(lease=no_lease):
            run_robot(suite).assert_passed()


class TestFailures:
    @pytest.fixture
    def served(self):
        root = Composite(children={"dut": Composite(children={"power": MockPower()})})
        with serve(root) as client, use_client(client):
            yield client

    def test_mismatch_missing_and_live_class(self, tmp_path, served, run_robot):
        suite = write(
            tmp_path,
            "failures.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    auto_import=False
            Library    jumpstarter_robot.Driver    dut.power    client=SHELL_CLIENT    prefix=Wrong    AS    wrong
            Library    jumpstarter_robot.Driver    dut.camera    client=POWER_CLIENT    required=False
            ...    AS    dut.camera
            Library    jumpstarter_robot.Driver    dut.power    AS    dut.power

            *** Test Cases ***
            Class Mismatch
                Wrong Run Method    anything

            Optional Driver Is Missing
                ${ok}=    Run Keyword And Return Status    Dut Camera On
                Should Not Be True    ${ok}
                Dut Camera On

            Live Class Discovery
                Dut Power On
                ${status}=    Dut Power Status
                Should Be Equal    ${status}    on
            """.replace("SHELL_CLIENT", SHELL).replace("POWER_CLIENT", POWER),
        )
        run = run_robot(suite)
        assert run.status("Class Mismatch") == "FAIL"
        assert f"is {POWER}, but this library was generated for {SHELL}" in run.message("Class Mismatch")
        assert run.status("Optional Driver Is Missing") == "FAIL"
        assert "optional driver is not on exporter 'test'" in run.message("Optional Driver Is Missing")
        assert run.status("Live Class Discovery") == "PASS"
