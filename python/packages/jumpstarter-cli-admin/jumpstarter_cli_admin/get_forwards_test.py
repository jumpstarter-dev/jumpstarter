"""`jmp admin get` for forwards and ports (JEP-0016)."""

import json
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from click.testing import CliRunner
from jumpstarter_kubernetes import (
    ExportersV1Alpha1Api,
    LeasesV1Alpha1Api,
    LeaseTemplatesV1Alpha1Api,
    V1Alpha1Exporter,
    V1Alpha1ExporterList,
    V1Alpha1Lease,
    V1Alpha1LeaseForwardEndStatus,
    V1Alpha1LeaseList,
    V1Alpha1LeaseTemplate,
    V1Alpha1LeaseTemplateList,
)

from .get import get
from .get_members_test import IVI_TEMPLATE, MEMBER_LEASE, SCALAR_LEASE, WIDE, condition, metadata

FORWARDS = [
    {
        "name": "bt",
        "endpoints": [{"member": "headunit", "port": "rootcanal"}, {"member": "peer", "port": "controller"}],
    },
    {"name": "uart", "endpoints": [{"member": "headunit", "port": "uart"}, {"member": "peer", "port": "uart"}]},
]
MEMBERS = [
    {"name": "peer", "selector": {"matchLabels": {"device-type": "bt-peer"}}},
    {"name": "headunit", "selector": {"matchLabels": {"device-type": "aaos-headunit"}}},
]

FORWARD_LEASE = V1Alpha1Lease.from_dict(
    {
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "Lease",
        "metadata": metadata("headunit-peer-7c2"),
        "spec": {
            "clientRef": {"name": "test_client"},
            "duration": "45m",
            "selector": {},
            "members": MEMBERS,
            "forwards": FORWARDS,
            "templateRef": {"name": "headunit-peer", "generation": 2},
        },
        "status": {
            "ended": False,
            "members": [
                {"name": "peer", "state": "Bound", "exporterRef": {"name": "peer-1"}},
                {"name": "headunit", "state": "Bound", "exporterRef": {"name": "hu-1"}},
            ],
            "forwards": [
                {"name": "bt", "state": "Listening"},
                {"name": "uart", "state": "Failed", "message": "port 'uart' is not on exporter peer-1"},
            ],
            "conditions": [condition("ForwardsReady", "False", "Failed"), condition("Degraded", "True", "Failed")],
        },
    }
)

FORWARD_TEMPLATE = V1Alpha1LeaseTemplate.from_dict(
    {
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "LeaseTemplate",
        "metadata": metadata("headunit-peer"),
        "spec": {"members": MEMBERS, "forwards": FORWARDS, "defaults": {"duration": "45m"}},
    }
)

PORTS = [
    {"name": "rootcanal", "direction": "provides", "protocol": "hci-h4"},
    {"name": "can0", "direction": "connects", "attachment": "wired"},
]


def exporter(name: str, ports: list | None) -> V1Alpha1Exporter:
    device: dict = {"labels": {"jumpstarter.dev/name": "rootcanal"}, "uuid": "11111111-2222-3333-4444-555555555555"}
    if ports is not None:
        device["ports"] = ports
    return V1Alpha1Exporter.from_dict(
        {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Exporter",
            "metadata": metadata(name),
            "spec": {},
            "status": {"endpoint": "grpc://example.com:443", "exporterStatus": "Available", "devices": [device]},
        }
    )


def row(output: str, name: str) -> str:
    return next(line for line in output.splitlines() if line.startswith(name))


@patch.object(LeasesV1Alpha1Api, "list_leases")
@patch.object(LeasesV1Alpha1Api, "_load_kube_config")
def test_get_leases_shows_the_state_of_each_forward(_load_kube_config_mock, list_leases_mock: AsyncMock):
    list_leases_mock.return_value = V1Alpha1LeaseList(items=[FORWARD_LEASE, MEMBER_LEASE, SCALAR_LEASE])

    result = CliRunner().invoke(get, ["leases"], env=WIDE)

    assert result.exit_code == 0
    assert "FORWARDS" in result.output.splitlines()[0]
    assert "bt=Listening uart=Failed" in row(result.output, "headunit-peer-7c2")
    assert "peer=peer-1 headunit=hu-1" in row(result.output, "headunit-peer-7c2")
    # the leases without forwards keep their cells
    assert "Listening" not in row(result.output, "ivi-4fj2k") and "rpi-1" in row(result.output, "scalar-1")


@patch.object(LeasesV1Alpha1Api, "list_leases")
@patch.object(LeasesV1Alpha1Api, "_load_kube_config")
def test_get_leases_is_unchanged_when_no_lease_has_forwards(_load_kube_config_mock, list_leases_mock: AsyncMock):
    list_leases_mock.return_value = V1Alpha1LeaseList(items=[MEMBER_LEASE, SCALAR_LEASE])

    result = CliRunner().invoke(get, ["leases"], env=WIDE)

    assert result.exit_code == 0
    assert result.output.splitlines()[0].split() == [
        "NAME", "CLIENT", "SELECTOR", "EXPORTER", "DURATION", "STATUS", "REASON", "BEGIN", "END", "AGE",
    ]  # fmt: skip


@patch.object(LeasesV1Alpha1Api, "get_lease")
@patch.object(LeasesV1Alpha1Api, "_load_kube_config")
def test_get_lease_structured_output_carries_the_forwards(_load_kube_config_mock, get_lease_mock: AsyncMock):
    get_lease_mock.return_value = FORWARD_LEASE
    runner = CliRunner()

    as_json = runner.invoke(get, ["lease", "headunit-peer-7c2", "--output", "json"])
    as_yaml = runner.invoke(get, ["lease", "headunit-peer-7c2", "--output", "yaml"])
    as_table = runner.invoke(get, ["lease", "headunit-peer-7c2"], env=WIDE)

    assert as_json.exit_code == 0 and as_yaml.exit_code == 0
    for parsed in (json.loads(as_json.output), yaml.safe_load(as_yaml.output)):
        assert parsed["spec"]["forwards"] == FORWARDS
        assert parsed["status"]["forwards"] == [
            {"name": "bt", "state": "Listening"},
            {"name": "uart", "state": "Failed", "message": "port 'uart' is not on exporter peer-1"},
        ]
    assert "bt=Listening uart=Failed" in as_table.output


@patch.object(LeasesV1Alpha1Api, "get_lease")
@patch.object(LeasesV1Alpha1Api, "_load_kube_config")
def test_get_lease_structured_output_carries_the_ends_of_a_forward(_load_kube_config_mock, get_lease_mock: AsyncMock):
    ends = [
        {"member": "headunit", "state": "Ready", "sequence": 12, "setupTime": "2026-11-24T10:00:00Z"},
        {"member": "peer", "state": "Failed", "message": "address already in use", "sequence": 7},
    ]
    lease = FORWARD_LEASE.model_copy(deep=True)
    assert lease.status.forwards is not None
    lease.status.forwards[1].ends = [V1Alpha1LeaseForwardEndStatus.model_validate(end) for end in ends]
    get_lease_mock.return_value = lease
    runner = CliRunner()

    as_json = runner.invoke(get, ["lease", "headunit-peer-7c2", "--output", "json"])
    as_yaml = runner.invoke(get, ["lease", "headunit-peer-7c2", "--output", "yaml"])

    assert as_json.exit_code == 0 and as_yaml.exit_code == 0
    for parsed in (json.loads(as_json.output), yaml.safe_load(as_yaml.output)):
        assert parsed["status"]["forwards"][0] == {"name": "bt", "state": "Listening"}
        assert parsed["status"]["forwards"][1]["ends"] == ends


@patch.object(LeasesV1Alpha1Api, "get_lease")
@patch.object(LeasesV1Alpha1Api, "_load_kube_config")
def test_get_lease_without_forwards_has_no_forwards_keys(_load_kube_config_mock, get_lease_mock: AsyncMock):
    get_lease_mock.return_value = MEMBER_LEASE

    as_json = CliRunner().invoke(get, ["lease", "ivi-4fj2k", "--output", "json"])
    as_table = CliRunner().invoke(get, ["lease", "ivi-4fj2k"], env=WIDE)

    parsed = json.loads(as_json.output)
    assert "forwards" not in parsed["spec"] and "forwards" not in parsed["status"]
    assert "FORWARDS" not in as_table.output


@pytest.mark.parametrize("noun", ["leasetemplates", "lease-templates"])
@patch.object(LeaseTemplatesV1Alpha1Api, "list_lease_templates")
@patch.object(LeaseTemplatesV1Alpha1Api, "_load_kube_config")
def test_get_lease_templates_shows_the_forwards(_load_kube_config_mock, list_lease_templates_mock: AsyncMock, noun):
    list_lease_templates_mock.return_value = V1Alpha1LeaseTemplateList(items=[FORWARD_TEMPLATE, IVI_TEMPLATE])

    result = CliRunner().invoke(get, [noun], env=WIDE)

    assert result.exit_code == 0
    assert "FORWARDS" in result.output.splitlines()[0]
    assert "bt(headunit.rootcanal,peer.controller) uart(headunit.uart,peer.uart)" in row(
        result.output, "headunit-peer"
    )


@patch.object(LeaseTemplatesV1Alpha1Api, "list_lease_templates")
@patch.object(LeaseTemplatesV1Alpha1Api, "_load_kube_config")
def test_get_lease_templates_is_unchanged_when_no_template_has_forwards(
    _load_kube_config_mock, list_lease_templates_mock: AsyncMock
):
    list_lease_templates_mock.return_value = V1Alpha1LeaseTemplateList(items=[IVI_TEMPLATE])

    result = CliRunner().invoke(get, ["leasetemplates"], env=WIDE)

    assert result.output.splitlines()[0].split() == ["NAME", "MEMBERS", "DURATION", "DESCRIPTION", "AGE"]


@patch.object(LeaseTemplatesV1Alpha1Api, "get_lease_template")
@patch.object(LeaseTemplatesV1Alpha1Api, "_load_kube_config")
def test_get_one_lease_template_with_forwards(_load_kube_config_mock, get_lease_template_mock: AsyncMock):
    get_lease_template_mock.return_value = FORWARD_TEMPLATE
    runner = CliRunner()

    as_table = runner.invoke(get, ["leasetemplate", "headunit-peer"], env=WIDE)
    as_yaml = runner.invoke(get, ["leasetemplate", "headunit-peer", "--output", "yaml"])

    assert "bt(headunit.rootcanal,peer.controller)" in as_table.output
    assert yaml.safe_load(as_yaml.output)["spec"]["forwards"] == FORWARDS


@pytest.mark.parametrize("flags", [[], ["--devices"]])
@patch.object(ExportersV1Alpha1Api, "list_exporters")
@patch.object(ExportersV1Alpha1Api, "_load_kube_config")
def test_get_exporters_shows_the_ports(_load_kube_config_mock, list_exporters_mock: AsyncMock, flags):
    list_exporters_mock.return_value = V1Alpha1ExporterList(items=[exporter("hu-1", PORTS), exporter("old-1", None)])

    result = CliRunner().invoke(get, ["exporters", *flags], env=WIDE)

    assert result.exit_code == 0
    assert "PORTS" in result.output.splitlines()[0]
    assert "rootcanal:provides can0:connects:wired" in row(result.output, "hu-1")
    assert "provides" not in row(result.output, "old-1")


@pytest.mark.parametrize("flags", [[], ["--devices"]])
@patch.object(ExportersV1Alpha1Api, "list_exporters")
@patch.object(ExportersV1Alpha1Api, "_load_kube_config")
def test_get_exporters_is_unchanged_when_no_exporter_has_ports(
    _load_kube_config_mock, list_exporters_mock: AsyncMock, flags
):
    list_exporters_mock.return_value = V1Alpha1ExporterList(items=[exporter("old-1", None)])

    result = CliRunner().invoke(get, ["exporters", *flags], env=WIDE)

    assert "PORTS" not in result.output


@patch.object(ExportersV1Alpha1Api, "get_exporter")
@patch.object(ExportersV1Alpha1Api, "_load_kube_config")
def test_get_exporter_structured_output_carries_the_ports(_load_kube_config_mock, get_exporter_mock: AsyncMock):
    get_exporter_mock.return_value = exporter("hu-1", PORTS)
    runner = CliRunner()

    as_json = runner.invoke(get, ["exporter", "hu-1", "--output", "json"])
    as_table = runner.invoke(get, ["exporter", "hu-1"], env=WIDE)

    assert json.loads(as_json.output)["status"]["devices"][0]["ports"] == PORTS
    assert "rootcanal:provides" in as_table.output

    get_exporter_mock.return_value = exporter("old-1", None)
    as_json = runner.invoke(get, ["exporter", "old-1", "--output", "json"])
    assert "ports" not in json.loads(as_json.output)["status"]["devices"][0]
