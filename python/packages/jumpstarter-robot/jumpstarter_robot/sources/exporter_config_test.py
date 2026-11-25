from __future__ import annotations

import textwrap

import pytest

from ..tree import DeviceNode, DeviceTree
from . import from_exporter_config

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
        assert get_node(tree, "dut.power").client == POWER
        assert get_node(tree, "dut.storage").client == FLASHER
        assert get_node(tree, "group").kind == "composite" and get_node(tree, "group").client == COMPOSITE
        assert tree.source["kind"] == "exporter-config"
        assert tree.warnings == []

    def test_shell_methods_from_config(self, tmp_path):
        shell = get_node(from_exporter_config(write_config(tmp_path, FOREST)), "shell")
        assert shell.client == SHELL
        assert shell.methods == ["hello", "build"]


def test_unknown_alias_fails(tmp_path, monkeypatch):
    from jumpstarter.config.exporter import ExporterConfigV1Alpha1

    monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", tmp_path)
    with pytest.raises(FileNotFoundError):
        from_exporter_config("no-such-alias")
