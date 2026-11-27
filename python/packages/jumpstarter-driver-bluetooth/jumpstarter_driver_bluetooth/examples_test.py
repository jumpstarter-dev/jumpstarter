from pathlib import Path

import pytest
import yaml

from .driver import Bluetooth
from jumpstarter.config.exporter import ExporterConfigV1Alpha1

EXAMPLES = Path(__file__).parent.parent / "examples"


def entries(name: str) -> dict:
    """The Bluetooth entries of an example exporter configuration, by entry name."""
    config = ExporterConfigV1Alpha1.model_validate(yaml.safe_load((EXAMPLES / name).read_text()))
    return {
        role: instance.instantiate()
        for role, instance in config.export.items()
        if "jumpstarter_driver_bluetooth" in getattr(instance.root, "type", "")
    }


def bluetooth(name: str) -> dict[str, list[tuple[str, str, str | None]]]:
    """The ports those entries declare."""
    return {
        role: [(port.name, str(port.direction), port.protocol) for port in driver.ports]
        for role, driver in entries(name).items()
    }


@pytest.mark.parametrize("name", ["exporter-cuttlefish-phone.yaml", "exporter-cuttlefish-headunit.yaml"])
def test_a_virtual_device_has_its_bluetooth_as_a_port(name):
    assert bluetooth(name) == {"bluetooth": [("bluetooth", "connects", "bt-l2cap-bridge")]}


def test_an_edge_gateway_has_the_same_bridge_on_a_real_controller():
    assert bluetooth("exporter-edge-gateway.yaml") == {"bluetooth": [("bluetooth", "connects", "bt-l2cap-bridge")]}
    (entry,) = entries("exporter-edge-gateway.yaml").values()
    assert (entry._link.learn_name, entry._link.retry, entry._view) == (True, 5.0, None)


def test_every_device_has_one_entry_for_its_bluetooth():
    asked = {
        "exporter-cuttlefish-phone.yaml": "bridge",
        "exporter-cuttlefish-headunit.yaml": "bridge",
        "exporter-cuttlefish-phone-direct.yaml": "remote-controller",
        "exporter-cuttlefish-headunit-direct.yaml": "controller-port",
    }
    for name, link in asked.items():
        (entry,) = entries(name).values()
        assert isinstance(entry, Bluetooth) and entry.link == link
        # ... with the device's own stack behind it, asked over the ADB of the exporter
        assert entry._view is not None and entry._view.adb == "127.0.0.1:6520"
        assert {"link", "state", "capture"} <= set(entry.capabilities())


def test_two_virtual_devices_can_share_one_controller_simulator():
    assert bluetooth("exporter-cuttlefish-headunit-direct.yaml") == {"bluetooth": [("bluetooth", "provides", "hci-h4")]}
    assert bluetooth("exporter-cuttlefish-phone-direct.yaml") == {"bluetooth": [("bluetooth", "requires", "hci-h4")]}


def test_the_lease_templates_join_ports_the_example_exporters_declare():
    declared = {
        "phone": {"hwsim", "bluetooth"},
        "headunit": {"hwsim", "bluetooth", "web"},
        "tablet": {"bluetooth", "wifi-service"},
    }
    templates = list(yaml.safe_load_all((EXAMPLES / "lease-templates.yaml").read_text()))
    assert [template["metadata"]["name"] for template in templates if template] == [
        "phone-headunit",
        "phone-headunit-bluetooth",
        "tablet-headunit",
        "phone-headunit-direct",
        "phone-qemu-headunit",
    ]
    for template in filter(None, templates):
        members = {member["name"] for member in template["spec"]["members"]}
        for forward in template["spec"]["forwards"]:
            ends = forward["endpoints"]
            assert len(ends) == 2 and {end["member"] for end in ends} <= members
            for end in ends:
                assert end["port"] in declared[end["member"]], (template["metadata"]["name"], end)


def test_a_qemu_guest_has_its_hci_on_a_serial_port_and_no_adb():
    assert bluetooth("exporter-qemu-headunit.yaml") == {"bluetooth": [("bluetooth", "requires", "hci-h4")]}
    (entry,) = entries("exporter-qemu-headunit.yaml").values()
    link = entry._link
    assert (link.guest, link.guest_listens, link.new_controller, entry._view) == (
        "127.0.0.1:7301", True, "hardware-error", None,
    )  # fmt: skip
    # ... and the serial port it dials is the one the QEMU entry of the same exporter listens on
    config = yaml.safe_load((EXAMPLES / "exporter-qemu-headunit.yaml").read_text())
    assert config["export"]["qemu"]["config"]["serial_ports"]["bluetooth"] == {"listen": link.guest}
