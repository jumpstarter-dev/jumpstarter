from __future__ import annotations

import textwrap

import pytest

from ..tree import DeviceNode, DeviceTree
from . import ExporterClassError, from_exporter_class

POWER = "jumpstarter_driver_power.client.PowerClient"
SERIAL = "jumpstarter_driver_pyserial.client.PySerialClient"
STORAGE = "jumpstarter_driver_opendal.client.StorageMuxClient"
COMPOSITE = "jumpstarter_driver_composite.client.CompositeClient"


def get_node(tree: DeviceTree, path: str | list[str]) -> DeviceNode:
    """``tree.find(path)``, failing the test when there is no such node."""
    found = tree.find(path)
    assert found is not None, f"no node at {path!r}"
    return found


def write(path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))
    return path


def driver_interface(name: str, package: str, client: str | None = None, children: str = "") -> str:
    drivers = (
        f"""
          drivers:
            - language: python
              package: some-package
              clientClass: "{client}"
              driverClasses: ["x.driver:X"]"""
        if client
        else ""
    )
    return f"""
        apiVersion: jumpstarter.dev/v1alpha1
        kind: DriverInterface
        metadata:
          name: {name}
        spec:
          proto:
            package: {package}{drivers}{children}
        """


@pytest.fixture
def flat(tmp_path):
    """The jep-14 prototype: flat interfaces[], clientClass on every DriverInterface."""
    interfaces = tmp_path / "driverinterfaces"
    power = driver_interface("dev-jumpstarter-power-v1", "jumpstarter.interfaces.power.v1", POWER)
    write(interfaces / "power.yaml", power)
    write(interfaces / "storage.yaml", driver_interface("dev-jumpstarter-storage-mux-v1", "x.storage.v1", STORAGE))
    write(
        interfaces / "nested" / "network.yml",
        driver_interface(
            "dev-jumpstarter-network-v1", "x.network.v1", "jumpstarter_driver_network.client:NetworkClient"
        ),
    )
    exporter_class = write(
        tmp_path / "classes" / "example-board.yaml",
        """
        apiVersion: jumpstarter.dev/v1alpha1
        kind: ExporterClass
        metadata:
          name: example-board
        spec:
          selector:
            matchLabels:
              jumpstarter.dev/board-type: example-board
          interfaces:
            - name: power
              interfaceRef: dev-jumpstarter-power-v1
              required: true
            - name: storage
              interfaceRef: dev-jumpstarter-storage-mux-v1
              required: true
            - name: network
              interfaceRef: dev-jumpstarter-network-v1
              required: false
        """,
    )
    return exporter_class, interfaces


DUTLINK_CHILDREN = """
          children:
            - name: power
              interfaceRef: jumpstarter-driver-power-v1
              required: true
            - name: serial
              interfaceRef: jumpstarter-driver-serial-v1
              required: false
"""


@pytest.fixture
def nested(tmp_path):
    """JEP-0015: composite DriverInterfaces with children, ExporterClass extends."""
    interfaces = tmp_path / "interfaces"
    client = "jumpstarter_driver_power.client:PowerClient"
    power = driver_interface("jumpstarter-driver-power-v1", "jumpstarter.driver.power.v1", client)
    write(interfaces / "power.yaml", power)
    serial = driver_interface("jumpstarter-driver-serial-v1", "jumpstarter.driver.serial.v1", SERIAL)
    write(interfaces / "serial.yaml", serial)
    write(
        interfaces / "dutlink.yaml",
        driver_interface("jumpstarter-driver-dutlink-v1", "jumpstarter.driver.dutlink.v1", children=DUTLINK_CHILDREN),
    )
    network = driver_interface("jumpstarter-driver-network-v1", "jumpstarter.driver.network.v1")
    write(interfaces / "network.yaml", network)
    exporter_class = write(
        tmp_path / "classes.yaml",
        """
        apiVersion: jumpstarter.dev/v1alpha1
        kind: ExporterClass
        metadata:
          name: base-device
        spec:
          interfaces:
            - name: dutlink
              interfaceRef: jumpstarter-driver-dutlink-v1
              required: true
        ---
        apiVersion: jumpstarter.dev/v1alpha1
        kind: ExporterClass
        metadata:
          name: embedded-linux-board
        spec:
          extends: base-device
          interfaces:
            - name: network
              interfaceRef: jumpstarter-driver-network-v1
              required: false
        """,
    )
    return exporter_class, interfaces


class TestFlat:
    def test_tree(self, flat):
        exporter_class, interfaces = flat
        tree = from_exporter_class(exporter_class, [interfaces])
        assert tree.device_name == "ExampleBoard"
        assert tree.source == {"kind": "exporter-class", "ref": "example-board"}
        assert [(n.dotted, n.client, n.required) for n in tree.walk()] == [
            ("power", POWER, True),
            ("storage", STORAGE, True),
            ("network", "jumpstarter_driver_network.client.NetworkClient", False),
        ]
        assert get_node(tree, "power").interface == "jumpstarter.interfaces.power.v1"
        assert tree.interfaces["jumpstarter.interfaces.power.v1"]["driver_interface"] == "dev-jumpstarter-power-v1"
        assert tree.root_shape == "forest"

    def test_missing_driver_interface(self, flat):
        exporter_class, _ = flat
        with pytest.raises(ExporterClassError, match="dev-jumpstarter-power-v1.*--driver-interface-dir"):
            from_exporter_class(exporter_class)

    def test_missing_dir(self, flat, tmp_path):
        with pytest.raises(ExporterClassError, match="does not exist"):
            from_exporter_class(flat[0], [tmp_path / "nope"])


class TestNested:
    def test_missing_client_class_names_the_nodes_table(self, nested):
        exporter_class, interfaces = nested
        with pytest.raises(ExporterClassError) as error:
            from_exporter_class(exporter_class, [interfaces], client_name="rpi4")
        message = str(error.value)
        assert "'network'" in message and "jumpstarter-driver-network-v1" in message
        assert "[tool.jumpstarter.client.rpi4.nodes]" in message
        assert "--capture" in message

    def test_clients_fill_missing_hints(self, nested):
        exporter_class, interfaces = nested
        clients = {"network": "jumpstarter_driver_network.client:NetworkClient"}
        tree = from_exporter_class(exporter_class, [interfaces], clients=clients)
        assert [(n.dotted, n.kind, n.client, n.required) for n in tree.walk()] == [
            ("dutlink", "composite", COMPOSITE, True),
            ("dutlink.power", "driver", POWER, True),
            ("dutlink.serial", "driver", SERIAL, False),
            ("network", "driver", "jumpstarter_driver_network.client.NetworkClient", False),
        ]
        assert get_node(tree, "dutlink.power").interface == "jumpstarter.driver.power.v1"
        assert tree.root_shape == "forest"

    def test_pick_by_name(self, nested):
        exporter_class, interfaces = nested
        tree = from_exporter_class(f"{exporter_class}#base-device", [interfaces])
        assert [n.dotted for n in tree.walk()] == ["dutlink", "dutlink.power", "dutlink.serial"]
        assert tree.root_shape == "single_composite"
        assert tree.accessor(get_node(tree, "dutlink.power")) == ["power"]
        assert from_exporter_class(exporter_class, [interfaces], name="base-device").device_name == "BaseDevice"

    def test_optional_parent_makes_children_optional(self, nested, tmp_path):
        _, interfaces = nested
        exporter_class = write(
            tmp_path / "optional.yaml",
            """
            kind: ExporterClass
            metadata: {name: optional-dutlink}
            spec:
              interfaces:
                - {name: dutlink, interfaceRef: jumpstarter-driver-dutlink-v1, required: false}
            """,
        )
        tree = from_exporter_class(exporter_class, [interfaces])
        assert not any(n.required for n in tree.walk())


class TestErrors:
    def test_circular_extends(self, tmp_path):
        exporter_class = write(
            tmp_path / "loop.yaml",
            """
            kind: ExporterClass
            metadata: {name: a}
            spec: {extends: b}
            ---
            kind: ExporterClass
            metadata: {name: b}
            spec: {extends: a}
            """,
        )
        with pytest.raises(ExporterClassError, match="circular"):
            from_exporter_class(exporter_class, name="a")

    def test_ambiguous_class(self, tmp_path):
        exporter_class = write(
            tmp_path / "two.yaml",
            "kind: ExporterClass\nmetadata: {name: a}\n---\nkind: ExporterClass\nmetadata: {name: b}\n",
        )
        with pytest.raises(ExporterClassError, match="name the ExporterClass"):
            from_exporter_class(exporter_class)

    def test_extends_mismatch_and_relaxing_required(self, nested, tmp_path):
        _, interfaces = nested
        base = """
            kind: ExporterClass
            metadata: {name: base}
            spec:
              interfaces:
                - {name: power, interfaceRef: jumpstarter-driver-power-v1, required: true}
            ---
            kind: ExporterClass
            metadata: {name: child}
            spec:
              extends: base
              interfaces:
                - %s
            """
        mismatch = write(tmp_path / "m" / "m.yaml", base % "{name: power, interfaceRef: jumpstarter-driver-serial-v1}")
        with pytest.raises(ExporterClassError, match="extends declares"):
            from_exporter_class(mismatch, [interfaces], name="child")
        relaxed = write(tmp_path / "r" / "r.yaml", base % "{name: power, required: false}")
        with pytest.raises(ExporterClassError, match="optional"):
            from_exporter_class(relaxed, [interfaces], name="child")

    def test_child_can_require_an_optional_entry(self, nested, tmp_path):
        _, interfaces = nested
        exporter_class = write(
            tmp_path / "c" / "c.yaml",
            """
            kind: ExporterClass
            metadata: {name: base}
            spec:
              interfaces:
                - {name: power, interfaceRef: jumpstarter-driver-power-v1, required: false}
            ---
            kind: ExporterClass
            metadata: {name: child}
            spec:
              extends: base
              interfaces:
                - {name: power, required: true}
            """,
        )
        tree = from_exporter_class(exporter_class, [interfaces], name="child")
        assert get_node(tree, "power").required is True

    def test_missing_file_and_bad_yaml(self, tmp_path):
        with pytest.raises(ExporterClassError, match="does not exist"):
            from_exporter_class(tmp_path / "nope.yaml")
        bad = write(tmp_path / "bad.yaml", "kind: [unclosed\n")
        with pytest.raises(ExporterClassError, match="not valid YAML"):
            from_exporter_class(bad)

    def test_unrelated_bad_yaml_next_to_the_class_is_ignored(self, flat):
        exporter_class, interfaces = flat
        write(exporter_class.parent / "broken.yaml", "a: [\n")
        assert from_exporter_class(exporter_class, [interfaces]).find("power") is not None

    def test_extends_unknown_class(self, tmp_path):
        exporter_class = write(tmp_path / "x.yaml", "kind: ExporterClass\nmetadata: {name: a}\nspec: {extends: zz}\n")
        with pytest.raises(ExporterClassError, match="'zz' not found"):
            from_exporter_class(exporter_class)
