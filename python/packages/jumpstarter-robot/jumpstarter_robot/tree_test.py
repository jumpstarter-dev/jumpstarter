from __future__ import annotations

import json

import pytest

from .tree import HEADER, DeviceNode, DeviceTree, TreeError, infer_root_shape, pascal_case

POWER = "jumpstarter_driver_power.client.PowerClient"


def get_node(tree: DeviceTree, path: str | list[str]) -> DeviceNode:
    """``tree.find(path)``, failing the test when there is no such node."""
    found = tree.find(path)
    assert found is not None, f"no node at {path!r}"
    return found


def node(path, kind="driver", client=POWER, children=(), **kw):
    return DeviceNode(
        name=path[-1],
        path=list(path),
        kind=kind,
        bindings={"python": client} if client else {},
        children=list(children),
        **kw,
    )


def sample() -> DeviceTree:
    return DeviceTree(
        device_name="Rig",
        nodes=[
            node(
                ["dut"],
                kind="composite",
                client="jumpstarter_driver_composite.client.CompositeClient",
                children=[node(["dut", "power"], driver_type="jumpstarter_driver_power.driver.MockPower")],
            ),
            node(["shell"], client="jumpstarter_driver_shell.client.ShellClient", methods=["hello"], required=False),
            DeviceNode(name="cam", path=["cam"], kind="opaque", reason="not installed"),
        ],
        source={"kind": "exporter-config", "ref": "exporter.yaml"},
    )


class TestJson:
    def test_round_trip(self):
        tree = sample()
        loaded = DeviceTree.from_json(tree.to_json())
        assert loaded == tree
        assert loaded.to_json() == tree.to_json()

    def test_header_and_fields(self):
        data = json.loads(sample().to_json())
        assert data[HEADER] == "v1"
        assert data["root_shape"] == "forest"
        power = data["nodes"][0]["children"][0]
        assert power == {
            "name": "power",
            "path": ["dut", "power"],
            "kind": "driver",
            "interface": None,
            "driver_type": "jumpstarter_driver_power.driver.MockPower",
            "bindings": {"python": POWER},
            "required": True,
            "methods": [],
            "children": [],
        }
        assert data["nodes"][2]["reason"] == "not installed"

    def test_wrong_header(self):
        with pytest.raises(TreeError, match="not a device tree"):
            DeviceTree.from_dict({"nodes": []})
        with pytest.raises(TreeError, match="v1"):
            DeviceTree.from_dict({HEADER: "v2"})

    def test_bad_json(self):
        with pytest.raises(TreeError, match="not valid JSON"):
            DeviceTree.from_json("{")

    def test_bad_kind_and_shape(self):
        with pytest.raises(TreeError, match="kind"):
            DeviceTree.from_dict({HEADER: "v1", "nodes": [{"name": "a", "kind": "weird"}]})
        with pytest.raises(TreeError, match="root_shape"):
            DeviceTree.from_dict({HEADER: "v1", "root_shape": "tree", "nodes": []})
        with pytest.raises(TreeError, match="name"):
            DeviceTree.from_dict({HEADER: "v1", "nodes": [{"path": ["a"]}]})

    def test_minimal_node_defaults_path_from_parents(self):
        tree = DeviceTree.from_dict({HEADER: "v1", "nodes": [{"name": "dut", "children": [{"name": "power"}]}]})
        child = tree.nodes[0].children[0]
        assert child.path == ["dut", "power"]
        assert child.required is True and child.kind == "driver"

    def test_write_and_read(self, tmp_path):
        path = sample().write(tmp_path / "x.tree.json")
        assert DeviceTree.read(path) == sample()


class TestShape:
    def test_shapes(self):
        assert infer_root_shape([node(["power"])]) == ("single_driver", "power")
        assert infer_root_shape([node(["dut"], kind="composite")]) == ("single_composite", "dut")
        assert infer_root_shape([node(["a"]), node(["b"])]) == ("forest", None)
        assert infer_root_shape([DeviceNode(name="x", path=["x"], kind="opaque")]) == ("forest", None)

    def test_accessor_drops_single_composite_root(self):
        root = node(["dut"], kind="composite", children=[node(["dut", "power"])])
        tree = DeviceTree(device_name="D", nodes=[root])
        assert tree.root_shape == "single_composite"
        assert tree.accessor(root.children[0]) == ["power"]
        assert tree.accessor(root) == ["dut"]

    def test_accessor_keeps_forest_paths(self):
        tree = sample()
        assert tree.accessor(get_node(tree, "dut.power")) == ["dut", "power"]

    def test_accessor_of_unnamed_root(self):
        tree = DeviceTree(device_name="D", nodes=[DeviceNode(name="power", path=[])])
        assert tree.accessor(tree.nodes[0]) == ["power"]

    def test_walk_and_find(self):
        tree = sample()
        assert [n.dotted for n in tree.walk()] == ["dut", "dut.power", "shell", "cam"]
        assert get_node(tree, ["shell"]).methods == ["hello"]
        assert tree.find("nope") is None


def test_pascal_case():
    assert pascal_case("power-board") == "PowerBoard"
    assert pascal_case("power_board") == "PowerBoard"
    assert pascal_case("powerBoard") == "PowerBoard"
    assert pascal_case("rpi4.lab") == "Rpi4Lab"

