"""`jmp admin get` for the resources of multi-exporter leases."""

import json
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from click.testing import CliRunner
from jumpstarter_kubernetes import (
    ExporterClaimsV1Alpha1Api,
    ExportersV1Alpha1Api,
    LeasesV1Alpha1Api,
    LeaseTemplatesV1Alpha1Api,
    V1Alpha1Exporter,
    V1Alpha1ExporterClaim,
    V1Alpha1ExporterClaimList,
    V1Alpha1ExporterList,
    V1Alpha1Lease,
    V1Alpha1LeaseList,
    V1Alpha1LeaseTemplate,
    V1Alpha1LeaseTemplateList,
)
from kubernetes_asyncio.client.exceptions import ApiException

from .get import get

# Wide enough that a table row is never wrapped in the middle of a value.
WIDE = {"COLUMNS": "400"}


def metadata(name: str) -> dict:
    return {
        "creationTimestamp": "2024-01-01T21:00:00Z",
        "generation": 3,
        "managedFields": [],
        "name": name,
        "namespace": "testing",
        "resourceVersion": "7",
        "uid": "7a25eb81-6443-47ec-a62f-50165bffede8",
    }


def condition(type: str, status: str, reason: str) -> dict:
    return {
        "lastTransitionTime": "2024-01-01T21:00:00Z",
        "message": "",
        "observedGeneration": 1,
        "reason": reason,
        "status": status,
        "type": type,
    }


MEMBER_LEASE = V1Alpha1Lease.from_dict(
    {
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "Lease",
        "metadata": metadata("ivi-4fj2k"),
        "spec": {
            "clientRef": {"name": "test_client"},
            "duration": "4h",
            "selector": {},
            "members": [
                {"name": "headunit", "selector": {"matchLabels": {"device-type": "headunit"}}},
                {"name": "phone", "selector": {"matchLabels": {"device-type": "phone"}}, "optional": True},
                {"name": "ecu", "selector": {"matchLabels": {"device-type": "ecu"}}},
            ],
            "constraints": [{"members": ["headunit", "ecu"], "sameLabel": "bus"}],
            "templateRef": {"name": "ivi", "generation": 3},
        },
        "status": {
            "ended": False,
            "beginTime": "2024-01-01T21:00:05Z",
            "bindAttempt": 2,
            "exclusionGroups": ["bench-7"],
            "members": [
                {"name": "headunit", "state": "Bound", "exporterRef": {"name": "hu-1"}},
                {"name": "phone", "state": "Omitted", "reason": "Excluded"},
                {"name": "ecu", "state": "Lost", "reason": "Offline", "exporterRef": {"name": "ecu-2"}},
            ],
            "conditions": [condition("Ready", "False", "MemberLost"), condition("Degraded", "True", "MemberLost")],
        },
    }
)

SCALAR_LEASE = V1Alpha1Lease.from_dict(
    {
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "Lease",
        "metadata": metadata("scalar-1"),
        "spec": {
            "clientRef": {"name": "test_client"},
            "duration": "5m",
            "selector": {"matchLabels": {"hardware": "rpi4"}},
        },
        "status": {
            "ended": False,
            "exporterRef": {"name": "rpi-1"},
            "conditions": [condition("Ready", "True", "Ready")],
        },
    }
)


@patch.object(LeasesV1Alpha1Api, "list_leases")
@patch.object(LeasesV1Alpha1Api, "_load_kube_config")
def test_get_leases_shows_the_exporter_of_each_role(_load_kube_config_mock, list_leases_mock: AsyncMock):
    list_leases_mock.return_value = V1Alpha1LeaseList(items=[MEMBER_LEASE, SCALAR_LEASE])

    result = CliRunner().invoke(get, ["leases"], env=WIDE)

    assert result.exit_code == 0
    member_row = next(line for line in result.output.splitlines() if line.startswith("ivi-4fj2k"))
    scalar_row = next(line for line in result.output.splitlines() if line.startswith("scalar-1"))
    assert "template=ivi" in member_row
    assert "headunit=hu-1 phone=(Omitted) ecu=ecu-2(Lost)" in member_row
    assert "MemberLost" in member_row
    # A scalar lease is listed as it always was.
    assert "hardware:rpi4" in scalar_row
    assert "rpi-1" in scalar_row
    assert "=" not in scalar_row


@patch.object(LeasesV1Alpha1Api, "get_lease")
@patch.object(LeasesV1Alpha1Api, "_load_kube_config")
def test_get_lease_structured_output_carries_the_members(_load_kube_config_mock, get_lease_mock: AsyncMock):
    get_lease_mock.return_value = MEMBER_LEASE
    runner = CliRunner()

    as_json = runner.invoke(get, ["lease", "ivi-4fj2k", "--output", "json"])
    as_yaml = runner.invoke(get, ["lease", "ivi-4fj2k", "--output", "yaml"])
    as_name = runner.invoke(get, ["lease", "ivi-4fj2k", "--output", "name"])

    assert as_json.exit_code == 0
    assert as_yaml.exit_code == 0
    for parsed in (json.loads(as_json.output), yaml.safe_load(as_yaml.output)):
        assert [m["name"] for m in parsed["spec"]["members"]] == ["headunit", "phone", "ecu"]
        assert parsed["spec"]["members"][1]["optional"] is True
        assert parsed["spec"]["constraints"] == [{"members": ["headunit", "ecu"], "sameLabel": "bus"}]
        assert parsed["spec"]["templateRef"] == {"name": "ivi", "generation": 3}
        assert parsed["status"]["members"] == [
            {"name": "headunit", "state": "Bound", "exporterRef": {"name": "hu-1"}},
            {"name": "phone", "state": "Omitted", "reason": "Excluded"},
            {"name": "ecu", "state": "Lost", "reason": "Offline", "exporterRef": {"name": "ecu-2"}},
        ]
        assert parsed["status"]["exclusionGroups"] == ["bench-7"]
        assert parsed["status"]["bindAttempt"] == 2
    assert as_name.output == "lease.jumpstarter.dev/ivi-4fj2k\n"


def exporter(name: str, group: str | None) -> V1Alpha1Exporter:
    return V1Alpha1Exporter.from_dict(
        {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Exporter",
            "metadata": metadata(name),
            "spec": {"exclusionGroup": group} if group is not None else {},
            "status": {
                "endpoint": "grpc://example.com:443",
                "devices": [{"labels": {"hardware": "rpi4"}, "uuid": "82a8ac0d-d7ff-4009-8948-18a3c5c607b1"}],
                "exporterStatus": "Available",
            },
        }
    )


@pytest.mark.parametrize("flags", [[], ["--devices"]])
@patch.object(ExportersV1Alpha1Api, "list_exporters")
@patch.object(ExportersV1Alpha1Api, "_load_kube_config")
def test_get_exporters_shows_the_exclusion_group(_load_kube_config_mock, list_exporters_mock: AsyncMock, flags):
    exporters = [exporter("hu-1", "bench-7"), exporter("rpi-1", None)]
    list_exporters_mock.return_value = V1Alpha1ExporterList(items=exporters)

    result = CliRunner().invoke(get, ["exporters", *flags], env=WIDE)

    assert result.exit_code == 0
    header, grouped, plain = result.output.splitlines()[:3]
    assert header.split()[-1] == "GROUP"
    assert grouped.split()[-1] == "bench-7"
    assert "bench-7" not in plain


@patch.object(ExportersV1Alpha1Api, "list_exporters")
@patch.object(ExportersV1Alpha1Api, "_load_kube_config")
def test_get_exporters_has_no_group_column_when_no_exporter_has_one(
    _load_kube_config_mock, list_exporters_mock: AsyncMock
):
    list_exporters_mock.return_value = V1Alpha1ExporterList(items=[exporter("rpi-1", None)])

    result = CliRunner().invoke(get, ["exporters"], env=WIDE)

    assert result.exit_code == 0
    assert "GROUP" not in result.output


@patch.object(ExportersV1Alpha1Api, "get_exporter")
@patch.object(ExportersV1Alpha1Api, "_load_kube_config")
def test_get_exporter_shows_its_exclusion_group(_load_kube_config_mock, get_exporter_mock: AsyncMock):
    get_exporter_mock.return_value = exporter("hu-1", "bench-7")
    runner = CliRunner()

    table = runner.invoke(get, ["exporter", "hu-1"], env=WIDE)
    as_json = runner.invoke(get, ["exporter", "hu-1", "--output", "json"])
    as_yaml = runner.invoke(get, ["exporter", "hu-1", "--output", "yaml"])

    assert table.exit_code == 0
    assert table.output.splitlines()[1].split()[-1] == "bench-7"
    assert json.loads(as_json.output)["spec"] == {"exclusionGroup": "bench-7"}
    assert yaml.safe_load(as_yaml.output)["spec"] == {"exclusionGroup": "bench-7"}


IVI_TEMPLATE = V1Alpha1LeaseTemplate.from_dict(
    {
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "LeaseTemplate",
        "metadata": metadata("ivi"),
        "spec": {
            "description": "AAOS head unit, phone, and body ECU",
            "members": [
                {"name": "headunit", "selector": {"matchLabels": {"device-type": "headunit"}}},
                {"name": "phone", "selector": {"matchLabels": {"device-type": "phone"}}, "optional": True},
                {"name": "ecu", "selector": {"matchLabels": {"device-type": "ecu"}}},
            ],
            "constraints": [{"members": ["headunit", "ecu"], "sameLabel": "bus"}],
            "defaults": {"duration": "4h0m0s"},
        },
    }
)

PROJECTION_TEMPLATE = V1Alpha1LeaseTemplate.from_dict(
    {
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "LeaseTemplate",
        "metadata": metadata("projection"),
        "spec": {
            "members": [
                {"name": "phone", "selector": {"matchLabels": {"device-type": "phone"}}},
                {"name": "headunit", "selector": {"matchLabels": {"device-type": "headunit"}}},
            ],
        },
    }
)


@pytest.mark.parametrize("noun", ["leasetemplates", "lease-templates", "leasetemplate", "lease-template"])
@patch.object(LeaseTemplatesV1Alpha1Api, "list_lease_templates")
@patch.object(LeaseTemplatesV1Alpha1Api, "_load_kube_config")
def test_get_lease_templates(_load_kube_config_mock, list_lease_templates_mock: AsyncMock, noun):
    list_lease_templates_mock.return_value = V1Alpha1LeaseTemplateList(items=[IVI_TEMPLATE, PROJECTION_TEMPLATE])

    result = CliRunner().invoke(get, [noun], env=WIDE)

    assert result.exit_code == 0
    header, ivi, projection = result.output.splitlines()[:3]
    assert header.split() == ["NAME", "MEMBERS", "DURATION", "DESCRIPTION", "AGE"]
    assert "headunit, phone*, ecu" in ivi
    assert "4h0m0s" in ivi
    assert "AAOS head unit, phone, and body ECU" in ivi
    assert "phone, headunit" in projection
    assert "*" not in projection


@patch.object(LeaseTemplatesV1Alpha1Api, "list_lease_templates")
@patch.object(LeaseTemplatesV1Alpha1Api, "_load_kube_config")
def test_get_lease_templates_structured_output(_load_kube_config_mock, list_lease_templates_mock: AsyncMock):
    list_lease_templates_mock.return_value = V1Alpha1LeaseTemplateList(items=[IVI_TEMPLATE, PROJECTION_TEMPLATE])
    runner = CliRunner()

    as_json = runner.invoke(get, ["leasetemplates", "--output", "json"])
    as_yaml = runner.invoke(get, ["leasetemplates", "--output", "yaml"])
    as_name = runner.invoke(get, ["leasetemplates", "--output", "name"])

    for parsed in (json.loads(as_json.output), yaml.safe_load(as_yaml.output)):
        assert parsed["kind"] == "LeaseTemplateList"
        ivi, projection = parsed["items"]
        assert ivi["kind"] == "LeaseTemplate"
        assert ivi["metadata"]["generation"] == 3
        assert ivi["spec"]["members"][1] == {
            "name": "phone",
            "selector": {"matchLabels": {"device-type": "phone"}},
            "optional": True,
        }
        assert ivi["spec"]["defaults"] == {"duration": "4h0m0s"}
        assert set(projection["spec"]) == {"members"}
    assert as_name.output == "leasetemplate.jumpstarter.dev/ivi\nleasetemplate.jumpstarter.dev/projection\n"


@patch.object(LeaseTemplatesV1Alpha1Api, "get_lease_template")
@patch.object(LeaseTemplatesV1Alpha1Api, "_load_kube_config")
def test_get_one_lease_template(_load_kube_config_mock, get_lease_template_mock: AsyncMock):
    get_lease_template_mock.return_value = IVI_TEMPLATE
    runner = CliRunner()

    table = runner.invoke(get, ["leasetemplate", "ivi"], env=WIDE)
    as_json = runner.invoke(get, ["leasetemplate", "ivi", "--output", "json"])

    assert "headunit, phone*, ecu" in table.output
    assert json.loads(as_json.output)["spec"]["description"] == "AAOS head unit, phone, and body ECU"
    get_lease_template_mock.assert_awaited_with("ivi")


@patch.object(LeaseTemplatesV1Alpha1Api, "list_lease_templates")
@patch.object(LeaseTemplatesV1Alpha1Api, "_load_kube_config")
def test_get_lease_templates_when_there_are_none(_load_kube_config_mock, list_lease_templates_mock: AsyncMock):
    list_lease_templates_mock.return_value = V1Alpha1LeaseTemplateList(items=[])

    result = CliRunner().invoke(get, ["leasetemplates"])

    assert result.exit_code == 0
    assert "No resources found" in result.output


@patch.object(LeaseTemplatesV1Alpha1Api, "get_lease_template")
@patch.object(LeaseTemplatesV1Alpha1Api, "_load_kube_config")
def test_get_lease_template_that_does_not_exist(_load_kube_config_mock, get_lease_template_mock: AsyncMock):
    error = ApiException(status=404, reason="Not Found")
    error.body = json.dumps(
        {"kind": "Status", "reason": "NotFound", "message": 'leasetemplates.jumpstarter.dev "nope" not found'}
    )
    get_lease_template_mock.side_effect = error

    result = CliRunner().invoke(get, ["leasetemplate", "nope"])

    assert result.exit_code == 1
    assert 'Error from server (NotFound): leasetemplates.jumpstarter.dev "nope" not found' in result.output


def claim(name: str, holds: dict) -> V1Alpha1ExporterClaim:
    return V1Alpha1ExporterClaim.from_dict(
        {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "ExporterClaim",
            "metadata": metadata(name),
            "spec": holds
            | {"leaseRef": {"name": "ivi-4fj2k", "uid": "7a25eb81-6443-47ec-a62f-50165bffede8", "attempt": 2}},
        }
    )


CLAIMS = V1Alpha1ExporterClaimList(
    items=[
        claim("exporter-5d41402abc4b2a76b9719d911017c592", {"exporter": "hu-1"}),
        claim("group-7d793037a0760186574b0282f2f435e7", {"exclusionGroup": "bench-7"}),
    ]
)


@pytest.mark.parametrize("noun", ["exporterclaims", "exporter-claims", "exporterclaim", "exporter-claim"])
@patch.object(ExporterClaimsV1Alpha1Api, "list_exporter_claims")
@patch.object(ExporterClaimsV1Alpha1Api, "_load_kube_config")
def test_get_exporter_claims(_load_kube_config_mock, list_exporter_claims_mock: AsyncMock, noun):
    list_exporter_claims_mock.return_value = CLAIMS

    result = CliRunner().invoke(get, [noun], env=WIDE)

    assert result.exit_code == 0
    header, of_exporter, of_group = result.output.splitlines()[:3]
    assert header.split() == ["NAME", "EXPORTER", "GROUP", "LEASE", "ATTEMPT", "AGE"]
    assert of_exporter.split()[:4] == ["exporter-5d41402abc4b2a76b9719d911017c592", "hu-1", "ivi-4fj2k", "2"]
    assert of_group.split()[:4] == ["group-7d793037a0760186574b0282f2f435e7", "bench-7", "ivi-4fj2k", "2"]
    # The group of a group claim stands under GROUP, not under EXPORTER.
    assert of_group.index("bench-7") == header.index("GROUP")


@patch.object(ExporterClaimsV1Alpha1Api, "get_exporter_claim")
@patch.object(ExporterClaimsV1Alpha1Api, "list_exporter_claims")
@patch.object(ExporterClaimsV1Alpha1Api, "_load_kube_config")
def test_get_exporter_claims_structured_output(
    _load_kube_config_mock, list_exporter_claims_mock: AsyncMock, get_exporter_claim_mock: AsyncMock
):
    list_exporter_claims_mock.return_value = CLAIMS
    get_exporter_claim_mock.return_value = CLAIMS.items[1]
    runner = CliRunner()

    as_json = runner.invoke(get, ["exporterclaims", "--output", "json"])
    as_yaml = runner.invoke(get, ["exporterclaims", "--output", "yaml"])
    as_name = runner.invoke(get, ["exporterclaims", "--output", "name"])
    one = runner.invoke(get, ["exporterclaim", "group-7d793037a0760186574b0282f2f435e7", "--output", "json"])

    lease_ref = {"name": "ivi-4fj2k", "uid": "7a25eb81-6443-47ec-a62f-50165bffede8", "attempt": 2}
    for parsed in (json.loads(as_json.output), yaml.safe_load(as_yaml.output)):
        assert parsed["kind"] == "ExporterClaimList"
        assert [item["spec"] for item in parsed["items"]] == [
            {"exporter": "hu-1", "leaseRef": lease_ref},
            {"exclusionGroup": "bench-7", "leaseRef": lease_ref},
        ]
    assert as_name.output.splitlines() == [
        "exporterclaim.jumpstarter.dev/exporter-5d41402abc4b2a76b9719d911017c592",
        "exporterclaim.jumpstarter.dev/group-7d793037a0760186574b0282f2f435e7",
    ]
    assert json.loads(one.output)["spec"]["exclusionGroup"] == "bench-7"


def test_the_extra_names_of_a_noun_are_not_listed_twice_in_the_help():
    result = CliRunner().invoke(get, ["--help"])

    assert result.exit_code == 0
    assert "leasetemplate " in result.output
    assert "lease-templates" not in result.output
    assert "exporterclaims" not in result.output
