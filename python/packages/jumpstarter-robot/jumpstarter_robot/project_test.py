from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from .project import Project, ProjectError, find_project

POWER = "jumpstarter_driver_power.client.PowerClient"
SHELL = "jumpstarter_driver_shell.client.ShellClient"

ROBOT_TOML = """\
# robot.toml
paths = ["tests"]
output-dir = "results"

[variables]
JMP_SELECTOR = ""

[tool.jumpstarter.client]
roles = true                    # detect identical subtrees
include = ["dut.*"]
config = "lab"

[tool.jumpstarter.client.dut]  # one table per client
exporter-config = "exporter.yaml"
resource = "client.resource"

[tool.jumpstarter.client.dut.aliases]
"dutlink.power" = "Main Power"

[tool.jumpstarter.client.rpi4]
selector = "board=rpi4"
config = "configs/ci-client.yaml"
exclude = "*.ssh"
roles = false

[tool.jumpstarter.client.local]
exporter-config = "mock"
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))
    return path


@pytest.fixture
def project(tmp_path) -> Path:
    return write(tmp_path / "robot.toml", ROBOT_TOML)


class TestLoad:
    def test_clients_and_defaults(self, project, tmp_path):
        loaded = Project.load(project)
        assert list(loaded.clients) == ["dut", "rpi4", "local"]
        assert loaded.defaults == {"roles": True, "include": ["dut.*"], "config": "lab"}

        dut = loaded.client("dut")
        assert dut.session == "dut"
        assert dut.exporter_config == tmp_path / "exporter.yaml"
        assert dut.resource == tmp_path / "client.resource"
        assert dut.aliases == {"dutlink.power": "Main Power"}
        assert dut.roles is True and dut.include == ["dut.*"] and dut.config == "lab"

    def test_aliases_and_paths(self, project, tmp_path):
        local = Project.load(project).client("local")
        assert local.exporter_config == "mock"  # an alias stays an alias
        assert local.device_name == "Local"

    def test_unknown_client(self, project):
        with pytest.raises(ProjectError, match="no client 'nope'.*dut, rpi4, local"):
            Project.load(project).client("nope")

    @pytest.mark.parametrize(
        "body, message",
        [
            ("[tool.jumpstarter.client]\nbogus = 1\n", "unknown key 'bogus'"),
            ("[tool.jumpstarter.client.dut]\ntoken = 'abc'\n", "credentials do not belong"),
            ("[tool.jumpstarter.client\n", "not valid TOML"),
        ],
    )
    def test_errors(self, tmp_path, body, message):
        with pytest.raises(ProjectError, match=message):
            Project.load(write(tmp_path / "robot.toml", body))


class TestFind:
    def test_nearest_upwards(self, project, tmp_path):
        nested = tmp_path / "tests" / "deep"
        nested.mkdir(parents=True)
        assert find_project(nested) == project.resolve()
