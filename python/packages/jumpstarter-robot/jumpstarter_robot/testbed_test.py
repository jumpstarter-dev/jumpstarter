from __future__ import annotations

from datetime import timedelta

import pytest

from .conftest import write
from .errors import JumpstarterRobotError
from .session_library import SessionConfig
from .testbed import apply, settings_for


class TestSettingsFor:
    def test_shared_then_session_keys(self):
        testbed = {
            "config": "lab",
            "reuse-lease": False,
            "sessions": {"rpi4": {"selector": "board=rpi4", "config": "eu"}},
        }
        assert settings_for(testbed, "rpi4") == {"config": "eu", "reuse_lease": False, "selector": "board=rpi4"}
        assert settings_for(testbed, "other") == {"config": "lab", "reuse_lease": False}

    def test_no_testbed(self):
        assert settings_for(None, "rpi4") == {}

    @pytest.mark.parametrize(
        "testbed, message",
        [
            ({"bogus": "x"}, "unknown setting 'bogus'"),
            ({"sessions": {"rpi4": {"token": "x"}}}, "unknown setting 'token'"),
            ("board=rpi4", "must be a mapping"),
            ({"sessions": ["rpi4"]}, "must map session names"),
            ({"sessions": {"rpi4": "board=rpi4"}}, "session 'rpi4' must be a mapping"),
        ],
    )
    def test_invalid(self, testbed, message):
        with pytest.raises(JumpstarterRobotError, match=message):
            settings_for(testbed, "rpi4")


class TestApply:
    def test_converts_values(self):
        config = SessionConfig(name="rpi4", exporter_config="exporter.yaml")
        policy = apply(
            config,
            {
                "selector": "board=rpi4",
                "exporter_config": "",
                "duration": "2 hours",
                "acquisition_timeout": "5 min",
                "release_grace": "3 s",
                "reuse_lease": "false",
                "allow_disabled": True,
                "on_lease_end": "fail",
            },
            {},
        )
        assert config.selector == "board=rpi4"
        assert config.exporter_config is None  # an empty value clears the resource's default
        assert config.duration == timedelta(hours=2)
        assert config.acquisition_timeout == timedelta(minutes=5)
        assert config.release_grace == 3.0
        assert config.reuse_lease is False
        assert config.allow_disabled is True
        assert policy == "fail"

    def test_session_variable_on_the_command_line_wins(self):
        config = SessionConfig(name="rpi4", selector="board=from-cli")
        apply(config, {"selector": "board=from-testbed", "config": "lab"}, {"JMP_RPI4_SELECTOR": "board=from-cli"})
        assert config.selector == "board=from-cli"
        assert config.config == "lab"

    def test_default_session_uses_the_plain_variable_names(self):
        config = SessionConfig(name="default", selector="board=from-cli")
        apply(config, {"selector": "board=from-testbed"}, {"JMP_SELECTOR": "board=from-cli"})
        assert config.selector == "board=from-cli"


RESOURCE = """\
*** Settings ***
Library    jumpstarter_robot.Session    rpi4    exporter_config=${JMP_RPI4_EXPORTER_CONFIG}
...    selector=${JMP_RPI4_SELECTOR}    AS    session.rpi4

*** Variables ***
${JMP_RPI4_EXPORTER_CONFIG}    ${CURDIR}/exporter.yaml
${JMP_RPI4_SELECTOR}    ${EMPTY}
"""

SUITE = """\
*** Settings ***
Resource    client.resource

*** Test Cases ***
Config
    ${config}=    Evaluate    jumpstarter_robot.sessions.registered_config("rpi4")    modules=jumpstarter_robot
    Set Suite Metadata    selector    ${config.selector}
    Set Suite Metadata    exporter_config    ${config.exporter_config}
    Set Suite Metadata    config    ${config.config}
    Set Suite Metadata    reuse    ${config.reuse_lease}
"""

TESTBED = """\
JUMPSTARTER:
  config: lab-eu
  sessions:
    rpi4:
      selector: board=rpi4,lab=eu
      exporter-config: ""
      reuse-lease: false
BOARD_USER: root
"""


class TestTestbedFile:
    """A YAML variable file given with ``robot -V`` configures the sessions."""

    @pytest.fixture
    def project(self, tmp_path):
        write(tmp_path, "client.resource", RESOURCE)
        write(tmp_path, "testbed.yaml", TESTBED)
        return write(tmp_path, "suite.robot", SUITE)

    def metadata(self, run_robot, suite, **options):
        result = run_robot(suite, **options)
        assert result.rc == 0, {name: test.message for name, test in result.tests.items()}
        return result.result.suite.metadata

    def test_without_a_testbed_the_resource_decides(self, project, run_robot):
        metadata = self.metadata(run_robot, project)
        assert metadata["selector"] == "None"
        assert metadata["exporter_config"].endswith("exporter.yaml")

    def test_testbed_overrides_the_resource(self, project, run_robot):
        metadata = self.metadata(run_robot, project, variablefile=[str(project.parent / "testbed.yaml")])
        assert metadata["selector"] == "board=rpi4,lab=eu"
        assert metadata["exporter_config"] == "None"
        assert metadata["config"] == "lab-eu"
        assert metadata["reuse"] == "False"

    def test_session_variable_overrides_the_testbed(self, project, run_robot):
        metadata = self.metadata(
            run_robot,
            project,
            variablefile=[str(project.parent / "testbed.yaml")],
            variable=["JMP_RPI4_SELECTOR:board=pinned"],
        )
        assert metadata["selector"] == "board=pinned"
        assert metadata["config"] == "lab-eu"

    def test_bare_jumpstarter_import_takes_a_testbed_session(self, tmp_path, run_robot):
        write(tmp_path, "testbed.yaml", "JUMPSTARTER:\n  sessions:\n    default:\n      selector: board=x\n")
        suite = write(
            tmp_path,
            "suite.robot",
            """\
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter    auto_import=False

            *** Test Cases ***
            Config
                ${config}=    Evaluate    jumpstarter_robot.sessions.registered_config("default")
                ...    modules=jumpstarter_robot
                Set Suite Metadata    selector    ${config.selector}
            """,
        )
        metadata = self.metadata(run_robot, suite, variablefile=[str(tmp_path / "testbed.yaml")])
        assert metadata["selector"] == "board=x"
