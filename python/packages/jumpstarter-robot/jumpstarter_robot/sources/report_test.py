from __future__ import annotations

import json

import pytest
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_power.driver import MockPower
from jumpstarter_driver_shell.driver import Shell

from ..tree import DeviceNode, DeviceTree
from . import SourceError, from_describe, from_report
from jumpstarter.client.introspect import describe_client
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver, export

POWER = "jumpstarter_driver_power.client.PowerClient"
SHELL = "jumpstarter_driver_shell.client.ShellClient"
COMPOSITE = "jumpstarter_driver_composite.client.CompositeClient"


def get_node(tree: DeviceTree, path: str | list[str]) -> DeviceNode:
    """``tree.find(path)``, failing the test when there is no such node."""
    found = tree.find(path)
    assert found is not None, f"no node at {path!r}"
    return found


class Uninstalled(Driver):
    """A driver whose client package is missing on the client side."""

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_robot_missing_pkg.client.GoneClient"

    @export
    def ping(self) -> str:
        return "pong"


def exporter():
    return Composite(
        children={
            "dut": Composite(children={"power": MockPower()}),
            "shell": Shell(methods={"hello": "echo hi", "bye": "echo bye"}),
            "gone": Uninstalled(),
        }
    )


@pytest.fixture
def client():
    with serve(exporter()) as root:
        yield root


class TestFromReport:
    def test_walks_children_positionally(self, client):
        tree = from_report(client, device_name="Rig", ref="lab-1")
        assert tree.root_shape == "forest"
        assert [n.dotted for n in tree.walk()] == ["dut", "dut.power", "shell", "gone"]
        assert get_node(tree, "dut").kind == "composite"
        assert get_node(tree, "dut").client == COMPOSITE
        assert get_node(tree, "dut.power").client == POWER
        assert tree.source == {"kind": "report", "ref": "lab-1"}

    def test_shell_methods_come_from_the_driver(self, client):
        shell = get_node(from_report(client), "shell")
        assert shell.client == SHELL
        assert sorted(shell.methods) == ["bye", "hello"]

    def test_uninstalled_client_is_opaque_with_warning(self, client):
        tree = from_report(client)
        gone = get_node(tree, "gone")
        assert gone.kind == "opaque"
        assert "jumpstarter_robot_missing_pkg.client.GoneClient" in (gone.reason or "")
        assert any(w.startswith("gone:") for w in tree.warnings)

    def test_single_driver_root(self):
        with serve(MockPower()) as root:
            tree = from_report(root)
        assert tree.root_shape == "single_driver"
        (power,) = tree.nodes
        assert power.path == [] and power.name == "power"
        assert power.client == POWER

    def test_single_composite_entry(self):
        with serve(Composite(children={"rig": Composite(children={"power": MockPower()})})) as root:
            tree = from_report(root)
        assert tree.root_shape == "single_composite"
        assert tree.root_entry == "rig"
        assert tree.accessor(get_node(tree, "rig.power")) == ["power"]


class TestFromDescribe:
    def describe(self, client) -> dict:
        return {"lease": {"name": "l1", "exporter": "rpi4-01"}, "driver_tree": describe_client(client)}

    def test_lease_description(self, client, tmp_path):
        path = tmp_path / "describe.json"
        path.write_text(json.dumps(self.describe(client)))
        tree = from_describe(path)
        assert tree.device_name == "Rpi401"
        assert [n.dotted for n in tree.walk()] == ["dut", "dut.power", "shell", "gone"]
        assert get_node(tree, "dut.power").client == POWER
        assert get_node(tree, "gone").kind == "opaque"
        assert tree.source == {"kind": "describe", "ref": str(path)}

    def test_text_and_bare_forms(self, client):
        data = describe_client(client)
        assert get_node(from_describe(json.dumps(data)), "shell").client == SHELL
        assert [n.dotted for n in from_describe(data["drivers"]).walk()][:2] == ["dut", "dut.power"]

    def test_bare_root(self):
        with serve(MockPower()) as root:
            tree = from_describe(describe_client(root))
        assert tree.nodes[0].name == "power" and tree.nodes[0].path == []

    def test_errors(self):
        with pytest.raises(SourceError, match="jmp describe lease"):
            from_describe({"lease": {}})
        with pytest.raises(SourceError, match="not valid JSON"):
            from_describe("{nope")
        with pytest.raises(SourceError, match="driver_path"):
            from_describe([{"class": POWER}])
