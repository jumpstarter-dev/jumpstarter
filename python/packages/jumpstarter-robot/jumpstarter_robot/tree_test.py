from __future__ import annotations

from .tree import DeviceNode, DeviceTree

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
                children=[node(["dut", "power"])],
            ),
            node(["shell"], client="jumpstarter_driver_shell.client.ShellClient", methods=["hello"], required=False),
            DeviceNode(name="cam", path=["cam"], kind="opaque", reason="not installed"),
        ],
        source={"kind": "exporter-config", "ref": "exporter.yaml"},
    )


class TestTree:
    def test_walk_and_find(self):
        tree = sample()
        assert [n.dotted for n in tree.walk()] == ["dut", "dut.power", "shell", "cam"]
        assert get_node(tree, ["shell"]).methods == ["hello"]
        assert tree.find("nope") is None
