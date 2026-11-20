from __future__ import annotations

import json

import pytest

from ..render import render_resource
from ..tree import DeviceNode, DeviceTree
from . import SourceError, from_exporter_config, from_tree_json

POWER = "jumpstarter_driver_power.client.PowerClient"

# rust-core's resolver output for the exporter config below, hand-copied field for
# field (ResolvedDevice.device_name/roots, ResolvedNode.name/path/kind/children,
# NodeKind::Driver.driver_type/clients), with the Python binding as its "clients".
RUST_SHAPED = {
    "jumpstarter.dev/device-tree": "v1",
    "device_name": "ExampleRig",
    "nodes": [
        {
            "name": "dut",
            "path": ["dut"],
            "kind": "composite",
            "children": [
                {
                    "name": "power",
                    "path": ["dut", "power"],
                    "kind": "driver",
                    "interface": "jumpstarter.interfaces.power.v1.PowerInterface",
                    "driver_type": "jumpstarter_driver_power.driver.MockPower",
                    "bindings": {"python": POWER},
                }
            ],
        }
    ],
}

EXPORTER = """\
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata: {namespace: default, name: example-rig}
endpoint: ""
token: ""
export:
  dut:
    children:
      power:
        type: jumpstarter_driver_power.driver.MockPower
"""


def get_node(tree: DeviceTree, path: str | list[str]) -> DeviceNode:
    """``tree.find(path)``, failing the test when there is no such node."""
    found = tree.find(path)
    assert found is not None, f"no node at {path!r}"
    return found


def test_reads_a_rust_shaped_tree(tmp_path):
    path = tmp_path / "rig.tree.json"
    path.write_text(json.dumps(RUST_SHAPED))
    tree = from_tree_json(path)
    assert tree.root_shape == "single_composite" and tree.root_entry == "dut"
    assert get_node(tree, "dut.power").interface == "jumpstarter.interfaces.power.v1.PowerInterface"
    assert tree.source == {"kind": "tree", "ref": str(path)}
    assert "dut.power    client=" in render_resource(tree)


def test_matches_the_exporter_config_source(tmp_path):
    config = tmp_path / "exporter.yaml"
    config.write_text(EXPORTER)
    ours = from_exporter_config(config)
    path = tmp_path / "rig.tree.json"
    path.write_text(json.dumps(RUST_SHAPED))
    theirs = from_tree_json(path)
    assert ours.device_name == theirs.device_name
    assert (ours.root_shape, ours.root_entry) == (theirs.root_shape, theirs.root_entry)
    assert [(n.name, n.path, n.kind, n.client) for n in ours.walk()] == [
        (n.name, n.path, n.kind, n.client if n.kind == "driver" else get_node(ours, n.path).client)
        for n in theirs.walk()
    ]


def test_errors(tmp_path):
    with pytest.raises(SourceError, match="does not exist"):
        from_tree_json(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text('{"nodes": []}')
    with pytest.raises(SourceError, match="not a device tree"):
        from_tree_json(bad)
