from __future__ import annotations

import textwrap
import tomllib
from pathlib import Path

import pytest

from .project import Project, ProjectError, client_settings, find_project, token_warnings, write_nodes

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

# committed mapping
[tool.jumpstarter.client.rpi4.nodes]
"dutlink" = "jumpstarter_driver_power.client.PowerClient"   # the board
"dutlink.power" = "jumpstarter_driver_power.client.PowerClient"

[tool.jumpstarter.client.local]
exporter-config = "mock"
tree = "trees/local.tree.json"
exporter-class = "classes/board.yaml#board"
driver-interface-dir = ["interfaces"]
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
        assert dut.nodes is None

    def test_client_overrides_defaults(self, project, tmp_path):
        rpi4 = Project.load(project).client("rpi4")
        assert rpi4.selector == "board=rpi4"
        assert rpi4.config == tmp_path / "configs" / "ci-client.yaml"
        assert rpi4.exclude == ["*.ssh"] and rpi4.roles is False
        assert rpi4.nodes == {"dutlink": POWER, "dutlink.power": POWER}
        assert rpi4.resource_path == tmp_path / "client-rpi4.resource"
        assert rpi4.source_args() == {
            "exporter_config": None,
            "selector": "board=rpi4",
            "lease_name": None,
            "config": tmp_path / "configs" / "ci-client.yaml",
        }
        assert rpi4.nodes_ref() == "robot.toml [tool.jumpstarter.client.rpi4.nodes]"

    def test_aliases_and_paths(self, project, tmp_path):
        local = Project.load(project).client("local")
        assert local.exporter_config == "mock"  # an alias stays an alias
        assert local.tree == tmp_path / "trees" / "local.tree.json"
        assert local.exporter_class == f"{tmp_path / 'classes' / 'board.yaml'}#board"
        assert local.driver_interface_dirs == [tmp_path / "interfaces"]
        assert local.device_name == "Local"

    def test_env_config(self, tmp_path):
        settings = client_settings("ci", {"config": "env", "selector": "a=b"}, base_dir=tmp_path)
        assert settings.config == "env"

    def test_unknown_client(self, project):
        with pytest.raises(ProjectError, match="no client 'nope'.*dut, rpi4, local"):
            Project.load(project).client("nope")

    @pytest.mark.parametrize(
        "body, message",
        [
            ("[tool.jumpstarter.client]\nbogus = 1\n", "unknown key 'bogus'"),
            ("[tool.jumpstarter.client.dut]\nselectr = 'a'\n", "unknown key 'selectr'"),
            ("[tool.jumpstarter.client.dut]\ntoken = 'abc'\n", "credentials do not belong"),
            ("[tool.jumpstarter.client.include]\nselector = 'a'\n", "cannot name a client"),
            ("[tool.jumpstarter.client.dut]\nroles = 'yes'\n", "true or false"),
            ("[tool.jumpstarter.client.dut]\nselector = 3\n", "must be a string"),
            ("[tool.jumpstarter.client.dut]\ninclude = [1]\n", "list of strings"),
            ("[tool.jumpstarter.client.dut]\naliases = {a = 1}\n", "Keyword Prefix"),
            ("[tool.jumpstarter.client.dut]\nnodes = 'x'\n", "tree path = client class"),
            ("[tool.jumpstarter.client\n", "not valid TOML"),
        ],
    )
    def test_errors(self, tmp_path, body, message):
        with pytest.raises(ProjectError, match=message):
            Project.load(write(tmp_path / "robot.toml", body))

    @pytest.mark.parametrize("table", ["[tool.jumpstarter.client]", "[tool.jumpstarter.client.dut]"])
    def test_prefixes_setting_is_gone(self, tmp_path, table):
        # keyword prefixes are always the full tree path; an alias is the only way to choose another
        with pytest.raises(ProjectError, match=r"unknown key 'prefixes'"):
            Project.load(write(tmp_path / "robot.toml", f"{table}\nprefixes = 'full'\n"))

    def test_missing_file(self, tmp_path):
        with pytest.raises(ProjectError, match="does not exist"):
            Project.load(tmp_path / "robot.toml")

    def test_pyproject(self, tmp_path):
        path = write(
            tmp_path / "pyproject.toml",
            '[project]\nname = "x"\n\n[tool.jumpstarter.client.dut]\nselector = "a=b"\n',
        )
        assert Project.load(path).client("dut").selector == "a=b"


class TestFind:
    def test_nearest_upwards(self, project, tmp_path):
        nested = tmp_path / "tests" / "deep"
        nested.mkdir(parents=True)
        assert find_project(nested) == project.resolve()

    def test_robot_toml_wins(self, project, tmp_path):
        write(tmp_path / "pyproject.toml", "[tool.jumpstarter.client.x]\nselector = 'a'\n")
        assert find_project(tmp_path) == project.resolve()

    def test_pyproject_with_table_wins_over_bare_robot_toml(self, tmp_path):
        write(tmp_path / "robot.toml", 'paths = ["tests"]\n')
        pyproject = write(tmp_path / "pyproject.toml", "[tool.jumpstarter.client.x]\nselector = 'a'\n")
        assert find_project(tmp_path) == pyproject.resolve()

    def test_pyproject_without_table_is_skipped(self, tmp_path):
        write(tmp_path / "pyproject.toml", '[project]\nname = "x"\n')
        inner = tmp_path / "inner"
        inner.mkdir()
        robot = write(inner / "robot.toml", 'paths = ["tests"]\n')
        assert find_project(inner) == robot.resolve()
        assert find_project(tmp_path) is None or find_project(tmp_path) != robot.resolve()


class TestWriteNodes:
    def test_keeps_comments_and_order(self, project):
        mapping = {
            "dutlink": POWER,
            "dutlink.power": POWER,
            "shell": {"client": SHELL, "methods": ["build"]},
            "camera": {"client": POWER, "required": False},
        }
        write_nodes(project, "rpi4", mapping)
        text = project.read_text()
        assert text.startswith(ROBOT_TOML.split("[tool.jumpstarter.client.rpi4.nodes]")[0])
        assert '"dutlink" = "jumpstarter_driver_power.client.PowerClient"   # the board' in text
        assert "# committed mapping" in text
        assert '[tool.jumpstarter.client.local]\nexporter-config = "mock"' in text
        data = tomllib.loads(text)
        assert data["tool"]["jumpstarter"]["client"]["rpi4"]["nodes"] == mapping
        assert Project.load(project).client("rpi4").nodes == mapping

    def test_removes_stale_entries(self, project):
        write_nodes(project, "rpi4", {"dutlink.power": SHELL})
        assert Project.load(project).client("rpi4").nodes == {"dutlink.power": SHELL}
        assert "# the board" not in project.read_text()

    def test_unchanged_round_trip_is_identical(self, project):
        write_nodes(project, "rpi4", {"dutlink": POWER, "dutlink.power": POWER})
        assert project.read_text() == ROBOT_TOML

    def test_adds_nodes_to_existing_client(self, project):
        write_nodes(project, "dut", {"power": POWER}, settings={"selector": "ignored"})
        dut = Project.load(project).client("dut")
        assert dut.nodes == {"power": POWER}
        assert dut.selector is None  # settings only seed new clients

    def test_new_client_and_new_file(self, tmp_path):
        path = tmp_path / "robot.toml"
        write_nodes(path, "rpi4", {"power": POWER}, settings={"selector": "board=rpi4", "lease": None})
        text = path.read_text()
        assert "[tool]" not in text and "[tool.jumpstarter]" not in text
        assert '[tool.jumpstarter.client.rpi4]\nselector = "board=rpi4"' in text
        assert Project.load(path).client("rpi4").nodes == {"power": POWER}

    def test_new_client_next_to_others(self, project):
        write_nodes(project, "sim", {"can": POWER}, settings={"selector": "role=can-sim"})
        loaded = Project.load(project)
        assert list(loaded.clients) == ["dut", "rpi4", "local", "sim"]
        assert loaded.client("sim").nodes == {"can": POWER}

    def test_nested_layout_is_replaced(self, tmp_path):
        path = write(tmp_path / "robot.toml", f'[tool.jumpstarter.client.a.nodes.dut]\npower = "{POWER}"\n')
        write_nodes(path, "a", {"dut.power": POWER, "dut.shell": SHELL})
        assert Project.load(path).client("a").nodes == {"dut.power": POWER, "dut.shell": SHELL}

    def test_refuses_secrets_and_reserved_names(self, tmp_path):
        with pytest.raises(ProjectError, match="credentials"):
            write_nodes(tmp_path / "robot.toml", "a", {"x": POWER}, settings={"token": "abc"})
        with pytest.raises(ProjectError, match="cannot name a client"):
            write_nodes(tmp_path / "robot.toml", "roles", {"x": POWER})
        assert not (tmp_path / "robot.toml").exists()


class TestTokens:
    def test_committed_config_with_token_warns(self, tmp_path):
        write(tmp_path / "ci.yaml", "endpoint: grpc.example:443\ntoken: secret\n")
        settings = client_settings("ci", {"config": "ci.yaml"}, base_dir=tmp_path)
        (warning,) = token_warnings(settings)
        assert "contains a token" in warning and "JMP_TOKEN" in warning

    def test_no_warning_without_token_or_for_alias(self, tmp_path):
        write(tmp_path / "ci.yaml", "endpoint: grpc.example:443\n")
        assert token_warnings(client_settings("ci", {"config": "ci.yaml"}, base_dir=tmp_path)) == []
        assert token_warnings(client_settings("ci", {"config": "lab"}, base_dir=tmp_path)) == []
