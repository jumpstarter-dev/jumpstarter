from __future__ import annotations

import json
import textwrap

import pytest

from ..sessions import Session
from ..tree import DeviceNode, DeviceTree
from . import from_exporter_config, from_report

POWER = "jumpstarter_driver_power.client.PowerClient"
SHELL = "jumpstarter_driver_shell.client.ShellClient"
COMPOSITE = "jumpstarter_driver_composite.client.CompositeClient"
FLASHER = "jumpstarter_driver_opendal.client.StorageMuxFlasherClient"

HEADER = """\
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: {name}
endpoint: ""
token: ""
"""


def get_node(tree: DeviceTree, path: str | list[str]) -> DeviceNode:
    """``tree.find(path)``, failing the test when there is no such node."""
    found = tree.find(path)
    assert found is not None, f"no node at {path!r}"
    return found


def write_config(tmp_path, body: str, name: str = "lab-rig", file: str = "exporter.yaml"):
    path = tmp_path / file
    path.write_text(HEADER.format(name=name) + textwrap.dedent(body))
    return path


FOREST = """\
export:
  dut:
    type: jumpstarter_driver_composite.driver.Composite
    children:
      power:
        type: jumpstarter_driver_power.driver.MockPower
      storage:
        type: jumpstarter_driver_opendal.driver.MockStorageMuxFlasher
  group:
    children:
      inner:
        type: jumpstarter_driver_power.driver.MockPower
  main-power:
    ref: dut.power
  shell:
    type: jumpstarter_driver_shell.driver.Shell
    config:
      methods:
        hello: "echo hello $1"
        build:
          command: "true"
          description: build it
"""


class TestFromExporterConfig:
    def test_forest(self, tmp_path):
        tree = from_exporter_config(write_config(tmp_path, FOREST))
        assert tree.device_name == "LabRig"
        assert tree.root_shape == "forest"
        assert [n.dotted for n in tree.walk()] == [
            "dut",
            "dut.power",
            "dut.storage",
            "group",
            "group.inner",
            "main-power",
            "shell",
        ]
        assert get_node(tree, "dut").kind == "composite"
        assert get_node(tree, "dut").driver_type == "jumpstarter_driver_composite.driver.Composite"
        assert get_node(tree, "dut.power").client == POWER
        assert get_node(tree, "dut.power").driver_type == "jumpstarter_driver_power.driver.MockPower"
        assert get_node(tree, "dut.storage").client == FLASHER
        assert get_node(tree, "group").kind == "composite" and get_node(tree, "group").client == COMPOSITE
        assert tree.source["kind"] == "exporter-config"
        assert tree.warnings == []

    def test_proxy_resolves_to_its_target(self, tmp_path):
        proxy = get_node(from_exporter_config(write_config(tmp_path, FOREST)), "main-power")
        assert proxy.kind == "driver"
        assert proxy.client == POWER
        assert proxy.path == ["main-power"]

    def test_shell_methods_from_config(self, tmp_path):
        shell = get_node(from_exporter_config(write_config(tmp_path, FOREST)), "shell")
        assert shell.client == SHELL
        assert shell.methods == ["hello", "build"]

    def test_matches_live_report(self, tmp_path):
        """The offline tree has the same paths and classes as the served exporter."""
        path = write_config(tmp_path, FOREST)
        offline = from_exporter_config(path)
        session = Session.from_exporter_config(config=str(path))
        try:
            live = from_report(session.client)
        finally:
            session.close()
        assert [(n.dotted, n.kind, n.client, n.methods) for n in offline.walk()] == [
            (n.dotted, n.kind, n.client, n.methods) for n in live.walk()
        ]

    def test_relative_path_and_base_dir(self, tmp_path):
        write_config(tmp_path, FOREST)
        tree = from_exporter_config("exporter.yaml", base_dir=tmp_path)
        assert tree.find("dut.power") is not None

    def test_single_composite(self, tmp_path):
        body = """\
        export:
          rig:
            type: jumpstarter_driver_composite.driver.Composite
            children:
              power:
                type: jumpstarter_driver_power.driver.MockPower
        """
        tree = from_exporter_config(write_config(tmp_path, body))
        assert (tree.root_shape, tree.root_entry) == ("single_composite", "rig")
        assert tree.accessor(get_node(tree, "rig.power")) == ["power"]

    def test_single_driver(self, tmp_path):
        body = """\
        export:
          power:
            type: jumpstarter_driver_power.driver.MockPower
        """
        tree = from_exporter_config(write_config(tmp_path, body))
        assert (tree.root_shape, tree.root_entry) == ("single_driver", "power")

    def test_missing_driver_and_bad_ref_are_opaque(self, tmp_path):
        body = """\
        export:
          gone:
            type: jumpstarter_robot_missing_pkg.driver.Gone
          dangling:
            ref: nothere.power
          power:
            type: jumpstarter_driver_power.driver.MockPower
        """
        tree = from_exporter_config(write_config(tmp_path, body))
        assert get_node(tree, "gone").kind == "opaque"
        assert "cannot be imported" in (get_node(tree, "gone").reason or "")
        assert get_node(tree, "dangling").kind == "opaque"
        assert "not found" in (get_node(tree, "dangling").reason or "")
        assert len(tree.warnings) == 2
        assert tree.root_shape == "forest"

    def test_driver_children_are_kept(self, tmp_path):
        body = """\
        export:
          board:
            type: jumpstarter_driver_power.driver.MockPower
            children:
              aux:
                type: jumpstarter_driver_power.driver.MockPower
        """
        tree = from_exporter_config(write_config(tmp_path, body))
        assert get_node(tree, "board").kind == "driver"
        assert get_node(tree, "board.aux").client == POWER

    def test_never_constructs_drivers(self, tmp_path, monkeypatch):
        from jumpstarter_driver_power.driver import MockPower

        def boom(self, *args, **kwargs):
            raise AssertionError("driver constructed")

        monkeypatch.setattr(MockPower, "__init__", boom)
        tree = from_exporter_config(write_config(tmp_path, FOREST))
        assert get_node(tree, "dut.power").client == POWER

    def test_json_is_stable(self, tmp_path):
        path = write_config(tmp_path, FOREST)
        assert from_exporter_config(path).to_json() == from_exporter_config(path).to_json()
        assert json.loads(from_exporter_config(path).to_json())["nodes"][0]["name"] == "dut"


def test_unknown_alias_fails(tmp_path, monkeypatch):
    from jumpstarter.config.exporter import ExporterConfigV1Alpha1

    monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", tmp_path)
    with pytest.raises(FileNotFoundError):
        from_exporter_config("no-such-alias")
