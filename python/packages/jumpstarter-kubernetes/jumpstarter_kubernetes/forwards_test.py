"""The Kubernetes models of forwards and ports (JEP-0016)."""

import json

import yaml
from rich.console import Console
from rich.table import Table

from jumpstarter_kubernetes import (
    V1Alpha1DevicePort,
    V1Alpha1Exporter,
    V1Alpha1ExporterList,
    V1Alpha1ForwardEndpoint,
    V1Alpha1Lease,
    V1Alpha1LeaseForward,
    V1Alpha1LeaseForwardEndStatus,
    V1Alpha1LeaseForwardStatus,
    V1Alpha1LeaseList,
    V1Alpha1LeaseTemplate,
    V1Alpha1LeaseTemplateList,
)

METADATA = {
    "creationTimestamp": "2024-01-01T21:00:00Z",
    "generation": 1,
    "managedFields": [],
    "name": "headunit-peer-x1",
    "namespace": "default",
    "resourceVersion": "1",
    "uid": "7a25eb81-6443-47ec-a62f-50165bffede8",
}
FORWARDS = [
    {
        "name": "bt",
        "endpoints": [{"member": "headunit", "port": "rootcanal"}, {"member": "peer", "port": "controller"}],
    },
    {
        "name": "console",
        "endpoints": [
            {"member": "headunit", "port": "console"},
            {"member": "peer", "port": "console"},
            {"member": "logger", "port": "console"},
        ],
    },
]
MEMBERS = [
    {"name": "peer", "selector": {"matchLabels": {"device-type": "bt-peer"}}},
    {"name": "headunit", "selector": {"matchLabels": {"device-type": "aaos-headunit"}}},
    {"name": "logger", "selector": {"matchLabels": {"device-type": "logger"}}, "optional": True},
]


def lease_dict(forwards=True, statuses=None) -> dict:
    spec: dict = {"clientRef": {"name": "ci"}, "duration": "45m", "selector": {}, "members": MEMBERS}
    status: dict = {"ended": False, "conditions": []}
    if forwards:
        spec["forwards"] = FORWARDS
        status["forwards"] = (
            statuses
            if statuses is not None
            else [
                {"name": "bt", "state": "Connected"},
                {"name": "console", "state": "Failed", "message": "port 'console' is not on exporter hu-1"},
            ]
        )
    return {
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "Lease",
        "metadata": METADATA,
        "spec": spec,
        "status": status,
    }


def template_dict(forwards=True) -> dict:
    spec: dict = {"members": MEMBERS, "defaults": {"duration": "45m"}}
    if forwards:
        spec["forwards"] = FORWARDS
    return {
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "LeaseTemplate",
        "metadata": {**METADATA, "name": "headunit-peer"},
        "spec": spec,
    }


def exporter_dict(name="hu-1", ports=None) -> dict:
    device: dict = {"labels": {"jumpstarter.dev/name": "rootcanal"}, "uuid": "11111111-2222-3333-4444-555555555555"}
    if ports is not None:
        device["ports"] = ports
    return {
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "Exporter",
        "metadata": {**METADATA, "name": name},
        "spec": {},
        "status": {
            "endpoint": "grpc://example.com:443",
            "exporterStatus": "Available",
            "devices": [device, {"labels": {}, "uuid": "66666666-2222-3333-4444-555555555555"}],
        },
    }


PORTS = [
    {"name": "rootcanal", "direction": "provides", "protocol": "hci-h4"},
    {"name": "can0", "direction": "connects", "attachment": "wired"},
    {"name": "someip-sd", "direction": "connects", "attachment": "socket", "transport": "datagram"},
]


def render(model, **kwargs) -> str:
    table = Table(box=None)
    model.rich_add_columns(table, **kwargs)
    model.rich_add_rows(table, **kwargs)
    console = Console(width=300, record=True)
    console.print(table)
    return console.export_text()


class TestLease:
    def test_reads_spec_and_status_forwards(self):
        lease = V1Alpha1Lease.from_dict(lease_dict())
        assert lease.has_forwards()
        assert lease.spec.forwards == [
            V1Alpha1LeaseForward(
                name="bt",
                endpoints=[
                    V1Alpha1ForwardEndpoint(member="headunit", port="rootcanal"),
                    V1Alpha1ForwardEndpoint(member="peer", port="controller"),
                ],
            ),
            V1Alpha1LeaseForward.model_validate(FORWARDS[1]),
        ]
        assert lease.status.forwards == [
            V1Alpha1LeaseForwardStatus(name="bt", state="Connected"),
            V1Alpha1LeaseForwardStatus(
                name="console", state="Failed", message="port 'console' is not on exporter hu-1"
            ),
        ]
        assert lease.spec.forwards[1].summary() == "console(headunit.console,peer.console,logger.console)"

    def test_json_and_yaml_round_trip_the_cluster_form(self):
        lease = V1Alpha1Lease.from_dict(lease_dict())
        for parsed in (json.loads(lease.dump_json()), yaml.safe_load(lease.dump_yaml())):
            assert parsed["spec"]["forwards"] == FORWARDS
            # a status without a message has no message key
            assert parsed["status"]["forwards"][0] == {"name": "bt", "state": "Connected"}
            assert parsed["status"]["forwards"][1]["message"] == "port 'console' is not on exporter hu-1"

    def test_reads_and_prints_the_ends_of_a_forward_as_the_cluster_has_them(self):
        data = lease_dict()
        ends = [
            {
                "member": "headunit",
                "state": "Ready",
                "sequence": 1764000000123,
                "setupTime": "2026-11-24T10:00:00Z",
                "reportTime": "2026-11-24T10:00:01Z",
            },
            {"member": "peer", "state": "Failed", "message": "address already in use", "sequence": 7},
            {"member": "logger", "state": "Pending", "setupTime": "2026-11-24T10:00:00Z"},
        ]
        data["status"]["forwards"][1]["ends"] = ends
        lease = V1Alpha1Lease.from_dict(data)
        assert lease.status.forwards is not None
        assert lease.status.forwards[0].ends is None
        assert lease.status.forwards[1].ends == [
            V1Alpha1LeaseForwardEndStatus(
                member="headunit",
                state="Ready",
                sequence=1764000000123,
                setup_time="2026-11-24T10:00:00Z",
                report_time="2026-11-24T10:00:01Z",
            ),
            V1Alpha1LeaseForwardEndStatus(member="peer", state="Failed", message="address already in use", sequence=7),
            V1Alpha1LeaseForwardEndStatus(member="logger", state="Pending", setup_time="2026-11-24T10:00:00Z"),
        ]
        for parsed in (json.loads(lease.dump_json()), yaml.safe_load(lease.dump_yaml())):
            # a forward without ends has no ends key, and an end has only the fields that are set
            assert "ends" not in parsed["status"]["forwards"][0]
            assert parsed["status"]["forwards"][1]["ends"] == ends

    def test_lease_without_forwards_prints_as_before(self):
        lease = V1Alpha1Lease.from_dict(lease_dict(forwards=False))
        assert not lease.has_forwards() and lease.spec.forwards is None and lease.status.forwards is None
        parsed = json.loads(lease.dump_json())
        assert "forwards" not in parsed["spec"] and "forwards" not in parsed["status"]

    def test_table_has_the_state_of_each_forward_when_asked(self):
        lease = V1Alpha1Lease.from_dict(lease_dict())
        text = render(lease, forwards=True)
        assert "FORWARDS" in text and "bt=Connected console=Failed" in text
        # the table of before forwards
        assert "FORWARDS" not in render(lease) and "bt=Connected" not in render(lease)

    def test_forward_without_a_status_is_pending(self):
        lease = V1Alpha1Lease.from_dict(lease_dict(statuses=[]))
        assert lease.forwards_summary() == "bt=Pending console=Pending"

    def test_list_knows_whether_a_lease_has_forwards(self):
        with_forwards = V1Alpha1LeaseList(
            items=[V1Alpha1Lease.from_dict(lease_dict()), V1Alpha1Lease.from_dict(lease_dict(forwards=False))]
        )
        assert with_forwards.has_forwards()
        text = render(with_forwards, forwards=with_forwards.has_forwards())
        assert "FORWARDS" in text and "bt=Connected" in text
        without = V1Alpha1LeaseList(items=[V1Alpha1Lease.from_dict(lease_dict(forwards=False))])
        assert not without.has_forwards() and "FORWARDS" not in render(without, forwards=without.has_forwards())


class TestLeaseTemplate:
    def test_reads_forwards(self):
        template = V1Alpha1LeaseTemplate.from_dict(template_dict())
        assert template.has_forwards()
        assert [f.name for f in template.spec.forwards or []] == ["bt", "console"]
        assert template.forwards_summary() == (
            "bt(headunit.rootcanal,peer.controller) console(headunit.console,peer.console,logger.console)"
        )
        assert json.loads(template.dump_json())["spec"]["forwards"] == FORWARDS
        assert yaml.safe_load(template.dump_yaml())["spec"]["forwards"] == FORWARDS

    def test_template_without_forwards_prints_as_before(self):
        template = V1Alpha1LeaseTemplate.from_dict(template_dict(forwards=False))
        assert not template.has_forwards() and "forwards" not in json.loads(template.dump_json())["spec"]
        assert "FORWARDS" not in render(template)

    def test_table_and_list(self):
        templates = V1Alpha1LeaseTemplateList(
            items=[
                V1Alpha1LeaseTemplate.from_dict(template_dict()),
                V1Alpha1LeaseTemplate.from_dict(template_dict(forwards=False)),
            ]
        )
        assert templates.has_forwards()
        text = render(templates, forwards=True)
        assert "FORWARDS" in text and "bt(headunit.rootcanal,peer.controller)" in text
        assert "FORWARDS" not in render(templates)


class TestExporterPorts:
    def test_reads_the_ports_of_each_device(self):
        exporter = V1Alpha1Exporter.from_dict(exporter_dict(ports=PORTS))
        assert exporter.status is not None
        assert exporter.status.devices[0].ports == [
            V1Alpha1DevicePort(name="rootcanal", direction="provides", protocol="hci-h4"),
            V1Alpha1DevicePort(name="can0", direction="connects", attachment="wired"),
            V1Alpha1DevicePort(name="someip-sd", direction="connects", attachment="socket", transport="datagram"),
        ]
        assert exporter.status.devices[1].ports is None
        assert exporter.has_ports()
        assert exporter.ports_summary() == "rootcanal:provides can0:connects:wired someip-sd:connects:datagram"

    def test_json_and_yaml_round_trip_the_cluster_form(self):
        exporter = V1Alpha1Exporter.from_dict(exporter_dict(ports=PORTS))
        for parsed in (json.loads(exporter.dump_json()), yaml.safe_load(exporter.dump_yaml())):
            assert parsed["status"]["devices"][0]["ports"] == PORTS
            assert "ports" not in parsed["status"]["devices"][1]

    def test_exporter_without_ports_prints_as_before(self):
        exporter = V1Alpha1Exporter.from_dict(exporter_dict())
        assert not exporter.has_ports() and exporter.ports_summary() == ""
        devices = json.loads(exporter.dump_json())["status"]["devices"]
        assert devices[0] == {
            "labels": {"jumpstarter.dev/name": "rootcanal"},
            "uuid": "11111111-2222-3333-4444-555555555555",
        }
        assert "PORTS" not in render(exporter) and "PORTS" not in render(exporter, devices=True)

    def test_tables(self):
        exporter = V1Alpha1Exporter.from_dict(exporter_dict(ports=PORTS))
        text = render(exporter, ports=True)
        assert "PORTS" in text and "rootcanal:provides can0:connects:wired" in text
        # with the devices, each device has its own ports
        rows = render(exporter, devices=True, ports=True, groups=True).splitlines()
        assert "PORTS" in rows[0] and rows[0].rstrip().endswith("GROUP")
        assert "rootcanal:provides" in rows[1] and "rootcanal:provides" not in rows[2]

    def test_exporter_without_status_or_devices(self):
        data = exporter_dict()
        del data["status"]
        exporter = V1Alpha1Exporter.from_dict(data)
        assert not exporter.has_ports()
        assert "hu-1" in render(exporter, devices=True, ports=True)

    def test_list_knows_whether_an_exporter_has_ports(self):
        exporters = V1Alpha1ExporterList(
            items=[
                V1Alpha1Exporter.from_dict(exporter_dict(ports=PORTS)),
                V1Alpha1Exporter.from_dict(exporter_dict("old")),
            ]
        )
        assert exporters.has_ports()
        assert not V1Alpha1ExporterList(items=[V1Alpha1Exporter.from_dict(exporter_dict("old"))]).has_ports()
