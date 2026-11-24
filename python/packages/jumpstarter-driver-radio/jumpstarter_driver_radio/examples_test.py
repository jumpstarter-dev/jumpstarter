from pathlib import Path

import pytest
import yaml

from jumpstarter.config.exporter import ExporterConfigV1Alpha1

EXAMPLES = Path(__file__).parent.parent / "examples"


def radios(name: str) -> dict[str, list[tuple[str, str, str | None]]]:
    """The ports the radio drivers of an example exporter configuration declare, by driver name."""
    config = ExporterConfigV1Alpha1.model_validate(yaml.safe_load((EXAMPLES / name).read_text()))
    return {
        role: [(port.name, str(port.direction), port.protocol) for port in instance.instantiate().ports]
        for role, instance in config.export.items()
        if "jumpstarter_driver_radio" in getattr(instance.root, "type", "")
    }


@pytest.mark.parametrize("name", ["exporter-cuttlefish-phone.yaml", "exporter-cuttlefish-headunit.yaml"])
def test_a_virtual_device_has_its_wifi_and_its_bluetooth_as_ports(name):
    assert radios(name) == {
        "wifi": [("hwsim", "connects", "ieee80211-frames")],
        "bluetooth": [("bluetooth", "connects", "bt-l2cap-bridge")],
    }


def test_an_edge_gateway_has_a_real_controller_and_one_port_of_a_real_wifi():
    assert radios("exporter-edge-gateway.yaml") == {
        "bluetooth": [("bluetooth", "connects", "bt-l2cap-bridge")],
        "wifi": [("wifi-service", "requires", None)],
    }


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
    ]
    for template in filter(None, templates):
        members = {member["name"] for member in template["spec"]["members"]}
        for forward in template["spec"]["forwards"]:
            ends = forward["endpoints"]
            assert len(ends) == 2 and {end["member"] for end in ends} <= members
            for end in ends:
                assert end["port"] in declared[end["member"]], (template["metadata"]["name"], end)
