from __future__ import annotations

import pytest
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_power.driver import MockPower
from jumpstarter_driver_shell.driver import Shell

from ..tree import DeviceNode, DeviceTree
from . import from_report
from jumpstarter.common.utils import serve

POWER = "jumpstarter_driver_power.client.PowerClient"
SHELL = "jumpstarter_driver_shell.client.ShellClient"
COMPOSITE = "jumpstarter_driver_composite.client.CompositeClient"


def get_node(tree: DeviceTree, path: str | list[str]) -> DeviceNode:
    """``tree.find(path)``, failing the test when there is no such node."""
    found = tree.find(path)
    assert found is not None, f"no node at {path!r}"
    return found


def exporter():
    return Composite(
        children={
            "dut": Composite(children={"power": MockPower()}),
            "shell": Shell(methods={"hello": "echo hi", "bye": "echo bye"}),
        }
    )


@pytest.fixture
def client():
    with serve(exporter()) as root:
        yield root


class TestFromReport:
    def test_walks_children_positionally(self, client):
        tree = from_report(client, device_name="Rig", ref="lab-1")
        assert [n.dotted for n in tree.walk()] == ["dut", "dut.power", "shell"]
        assert get_node(tree, "dut").kind == "composite"
        assert get_node(tree, "dut").client == COMPOSITE
        assert get_node(tree, "dut.power").client == POWER
        assert tree.source == {"kind": "report", "ref": "lab-1"}

    def test_shell_methods_come_from_the_driver(self, client):
        shell = get_node(from_report(client), "shell")
        assert shell.client == SHELL
        assert sorted(shell.methods) == ["bye", "hello"]
