from __future__ import annotations

import tomllib

import pytest

from ..tree import DeviceNode, DeviceTree
from . import SourceError, from_toml_nodes, to_toml_nodes

POWER = "jumpstarter_driver_power.client.PowerClient"
SHELL = "jumpstarter_driver_shell.client.ShellClient"
SERIAL = "jumpstarter_driver_pyserial.client.PySerialClient"
COMPOSITE = "jumpstarter_driver_composite.client.CompositeClient"

TABLE = f"""
"dutlink"         = "{POWER}"
"dutlink.power"   = "{POWER}"
"dutlink.console" = "jumpstarter_driver_pyserial.client:PySerialClient"
"camera"          = {{ client = "{POWER}", required = false }}
"shell"           = {{ client = "{SHELL}", methods = ["build", "deploy"] }}
"rig.a.power"     = "{POWER}"
"""


def get_node(tree: DeviceTree, path: str | list[str]) -> DeviceNode:
    """``tree.find(path)``, failing the test when there is no such node."""
    found = tree.find(path)
    assert found is not None, f"no node at {path!r}"
    return found


def nodes():
    return tomllib.loads(TABLE)


class TestFromTomlNodes:
    def test_hand_written_forms(self):
        tree = from_toml_nodes(nodes(), device_name="Rpi4", ref="robot.toml")
        assert [n.dotted for n in tree.walk()] == [
            "dutlink",
            "dutlink.power",
            "dutlink.console",
            "camera",
            "shell",
            "rig",
            "rig.a",
            "rig.a.power",
        ]
        assert tree.device_name == "Rpi4"
        assert tree.source == {"kind": "nodes", "ref": "robot.toml"}
        assert get_node(tree, "dutlink").kind == "driver"
        assert get_node(tree, "dutlink.console").client == SERIAL  # module:Class is normalized
        assert get_node(tree, "camera").required is False
        assert get_node(tree, "shell").methods == ["build", "deploy"]

    def test_parents_are_implied_composites(self):
        tree = from_toml_nodes(nodes())
        for path in ("rig", "rig.a"):
            node = get_node(tree, path)
            assert node.kind == "composite" and node.client is None

    def test_parent_listed_after_child(self):
        tree = from_toml_nodes({"dut.power": POWER, "dut": SHELL})
        assert [n.dotted for n in tree.walk()] == ["dut", "dut.power"]
        assert get_node(tree, "dut").client == SHELL and get_node(tree, "dut").kind == "driver"

    def test_nested_tables_group_children(self):
        tree = from_toml_nodes(tomllib.loads(f'[dut]\npower = "{POWER}"\nshell = {{ client = "{SHELL}" }}\n'))
        assert [n.dotted for n in tree.walk()] == ["dut", "dut.power", "dut.shell"]

    def test_methods_as_comma_string(self):
        tree = from_toml_nodes({"shell": {"client": SHELL, "methods": "a, b"}})
        assert get_node(tree, "shell").methods == ["a", "b"]

    def test_composite_client_is_composite(self):
        assert get_node(from_toml_nodes({"g": COMPOSITE}), "g").kind == "composite"

    def test_shapes(self):
        assert from_toml_nodes({"power": POWER}).root_shape == "single_driver"
        assert from_toml_nodes({"rig.power": POWER}).root_shape == "single_composite"
        assert from_toml_nodes(nodes()).root_shape == "forest"

    @pytest.mark.parametrize(
        "table, message",
        [
            ({"a": {"client": POWER, "color": "red"}}, "unknown key"),
            ({"a": {"client": ""}}, "client must be"),
            ({"a": {"client": POWER, "required": "no"}}, "required must be"),
            ({"a": {"client": POWER, "methods": [1]}}, "methods must be"),
            ({"a": 3}, "must be a class path"),
            ({"a..b": POWER}, "not a dotted tree path"),
            ({"a.b": POWER, "a": {"b": POWER}}, "listed twice"),
        ],
    )
    def test_errors(self, table, message):
        with pytest.raises(SourceError, match=message):
            from_toml_nodes(table)


class TestToTomlNodes:
    def test_round_trip(self):
        tree = from_toml_nodes(nodes())
        mapping = to_toml_nodes(tree)
        assert mapping == {
            "dutlink": POWER,
            "dutlink.power": POWER,
            "dutlink.console": SERIAL,
            "camera": {"client": POWER, "required": False},
            "shell": {"client": SHELL, "methods": ["build", "deploy"]},
            "rig.a.power": POWER,
        }
        assert to_toml_nodes(from_toml_nodes(mapping)) == mapping

    def test_empty_composite_is_kept(self):
        assert to_toml_nodes(from_toml_nodes({"g": COMPOSITE})) == {"g": COMPOSITE}

    def test_unnamed_root_cannot_be_written(self):
        from ..tree import DeviceNode, DeviceTree

        tree = DeviceTree(device_name="D", nodes=[DeviceNode(name="power", path=[], bindings={"python": POWER})])
        with pytest.raises(SourceError, match="unnamed root"):
            to_toml_nodes(tree)
