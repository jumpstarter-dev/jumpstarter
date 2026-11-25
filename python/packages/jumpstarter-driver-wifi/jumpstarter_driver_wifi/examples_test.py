from pathlib import Path

import pytest
import yaml

from jumpstarter.config.exporter import ExporterConfigV1Alpha1

EXAMPLES = Path(__file__).parent.parent / "examples"


def wifi(name: str) -> dict[str, list[tuple[str, str, str | None]]]:
    """The ports the Wi-Fi drivers of an example exporter configuration declare, by driver name."""
    config = ExporterConfigV1Alpha1.model_validate(yaml.safe_load((EXAMPLES / name).read_text()))
    return {
        role: [(port.name, str(port.direction), port.protocol) for port in instance.instantiate().ports]
        for role, instance in config.export.items()
        if "jumpstarter_driver_wifi" in getattr(instance.root, "type", "")
    }


@pytest.mark.parametrize("name", ["exporter-cuttlefish-phone.yaml", "exporter-cuttlefish-headunit.yaml"])
def test_a_virtual_device_has_its_wifi_as_a_port(name):
    assert wifi(name) == {"wifi": [("hwsim", "connects", "ieee80211-frames")]}


def test_an_edge_gateway_has_one_port_of_a_real_wifi():
    assert wifi("exporter-edge-gateway.yaml") == {"wifi": [("wifi-service", "requires", None)]}


def test_the_lease_templates_join_ports_the_example_exporters_declare():
    declared = {"phone": {"hwsim"}, "headunit": {"hwsim", "web"}, "tablet": {"wifi-service"}}
    templates = list(yaml.safe_load_all((EXAMPLES / "lease-templates.yaml").read_text()))
    assert [template["metadata"]["name"] for template in templates if template] == [
        "phone-headunit-wifi",
        "tablet-headunit-wifi",
    ]
    for template in filter(None, templates):
        members = {member["name"] for member in template["spec"]["members"]}
        for forward in template["spec"]["forwards"]:
            ends = forward["endpoints"]
            assert len(ends) == 2 and {end["member"] for end in ends} <= members
            for end in ends:
                assert end["port"] in declared[end["member"]], (template["metadata"]["name"], end)
