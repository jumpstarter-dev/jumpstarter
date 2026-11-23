from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jumpstarter_kubernetes import (
    ExporterClaimsV1Alpha1Api,
    V1Alpha1ExporterClaim,
    V1Alpha1ExporterClaimList,
)


def claim_dict(name: str, holds: dict) -> dict:
    return {
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "ExporterClaim",
        "metadata": {
            "creationTimestamp": "2021-10-01T00:00:00Z",
            "generation": 1,
            "name": name,
            "namespace": "default",
            "resourceVersion": "5",
            "uid": "0b0f0e0c-6443-47ec-a62f-50165bffede8",
        },
        "spec": holds
        | {"leaseRef": {"name": "ivi-4fj2k", "uid": "7a25eb81-6443-47ec-a62f-50165bffede8", "attempt": 2}},
    }


EXPORTER_CLAIM = claim_dict("exporter-5d41402abc4b2a76b9719d911017c592", {"exporter": "hu-1"})
GROUP_CLAIM = claim_dict("group-7d793037a0760186574b0282f2f435e7", {"exclusionGroup": "bench-7"})


def test_exporter_claim_from_dict():
    claim = V1Alpha1ExporterClaim.from_dict(EXPORTER_CLAIM)

    assert claim.spec.exporter == "hu-1"
    assert claim.spec.exclusion_group is None
    assert claim.spec.lease_ref.name == "ivi-4fj2k"
    assert claim.spec.lease_ref.uid == "7a25eb81-6443-47ec-a62f-50165bffede8"
    assert claim.spec.lease_ref.attempt == 2
    assert claim.model_dump(mode="json", by_alias=True)["spec"] == EXPORTER_CLAIM["spec"]


def test_group_claim_from_dict():
    claim = V1Alpha1ExporterClaim.from_dict(GROUP_CLAIM)

    assert claim.spec.exporter is None
    assert claim.spec.exclusion_group == "bench-7"
    assert claim.model_dump(mode="json", by_alias=True)["spec"] == GROUP_CLAIM["spec"]


def test_exporter_claim_rows():
    claims = V1Alpha1ExporterClaimList.from_dict({"items": [EXPORTER_CLAIM, GROUP_CLAIM]})
    table = MagicMock()
    with patch("jumpstarter_kubernetes.exporterclaims.time_since", return_value="5m"):
        claims.rich_add_rows(table)

    rows = [call.args for call in table.add_row.call_args_list]
    assert rows == [
        ("exporter-5d41402abc4b2a76b9719d911017c592", "hu-1", "", "ivi-4fj2k", "2", "5m"),
        ("group-7d793037a0760186574b0282f2f435e7", "", "bench-7", "ivi-4fj2k", "2", "5m"),
    ]


def test_exporter_claim_names():
    names = []
    V1Alpha1ExporterClaimList.from_dict({"items": [EXPORTER_CLAIM]}).rich_add_names(names)

    assert names == ["exporterclaim.jumpstarter.dev/exporter-5d41402abc4b2a76b9719d911017c592"]


@pytest.mark.anyio
async def test_list_and_get_exporter_claims():
    api = ExporterClaimsV1Alpha1Api("lab")
    api.api = MagicMock()
    api.api.list_namespaced_custom_object = AsyncMock(return_value={"items": [EXPORTER_CLAIM, GROUP_CLAIM]})
    api.api.get_namespaced_custom_object = AsyncMock(return_value=GROUP_CLAIM)

    claims = await api.list_exporter_claims()
    claim = await api.get_exporter_claim("group-7d793037a0760186574b0282f2f435e7")

    assert len(claims.items) == 2
    assert claim.spec.exclusion_group == "bench-7"
    api.api.list_namespaced_custom_object.assert_awaited_once_with(
        namespace="lab", group="jumpstarter.dev", plural="exporterclaims", version="v1alpha1"
    )
    api.api.get_namespaced_custom_object.assert_awaited_once_with(
        namespace="lab",
        group="jumpstarter.dev",
        plural="exporterclaims",
        version="v1alpha1",
        name="group-7d793037a0760186574b0282f2f435e7",
    )


def test_the_api_has_no_way_to_write_a_claim():
    # Only the controller writes claims.
    assert not [name for name in dir(ExporterClaimsV1Alpha1Api) if name.startswith(("create", "delete", "apply"))]
