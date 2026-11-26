"""End-to-end: plain ``robot.run`` against exporters served in this process."""

from __future__ import annotations

import pytest
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_power.driver import MockPower
from jumpstarter_driver_shell.driver import Shell

from . import sessions
from .conftest import write
from jumpstarter.common import ExporterStatus
from jumpstarter.config.env import JMP_DRIVERS_ALLOW, JMP_EXPORTER, JUMPSTARTER_HOST
from jumpstarter.exporter import Session as ExporterSession

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
    # auto-imported keywords carry the driver's full path, like a Driver import's
    Dut Power On
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


    def test_client_resource(self, project, run_robot):
        suite = write(project, "tests/static.robot", STATIC_TESTS)
        run_robot(suite).assert_passed()
        assert sessions.opened("rig") is None


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
