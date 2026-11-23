from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from jumpstarter_kubernetes import (
    LeaseTemplatesV1Alpha1Api,
    V1Alpha1LeaseTemplate,
    V1Alpha1LeaseTemplateList,
)

IVI_TEMPLATE = {
    "apiVersion": "jumpstarter.dev/v1alpha1",
    "kind": "LeaseTemplate",
    "metadata": {
        "creationTimestamp": "2021-10-01T00:00:00Z",
        "generation": 3,
        "name": "ivi",
        "namespace": "default",
        "resourceVersion": "12",
        "uid": "7a25eb81-6443-47ec-a62f-50165bffede8",
    },
    "spec": {
        "description": "AAOS head unit, phone, and body ECU",
        "docs": ["https://example.com/ivi"],
        "image": "https://example.com/ivi.png",
        "instructions": "Connect the phone before the test.",
        "members": [
            {"name": "headunit", "selector": {"matchLabels": {"device-type": "headunit"}}},
            {"name": "phone", "selector": {"matchLabels": {"device-type": "phone"}}, "optional": True},
            {"name": "ecu", "exporterRef": {"name": "ecu-2"}},
        ],
        "constraints": [{"members": ["headunit", "ecu"], "sameLabel": "bus"}],
        "defaults": {"duration": "4h0m0s", "sharedWith": ["ci"]},
    },
}

# The least a template can be: one member and nothing else.
MINIMAL_TEMPLATE = {
    "apiVersion": "jumpstarter.dev/v1alpha1",
    "kind": "LeaseTemplate",
    "metadata": {"name": "single", "namespace": "default"},
    "spec": {"members": [{"name": "board", "selector": {"matchLabels": {"board": "rpi4"}}}]},
}


def test_lease_template_from_dict():
    template = V1Alpha1LeaseTemplate.from_dict(IVI_TEMPLATE)

    assert template.metadata.name == "ivi"
    assert template.metadata.generation == 3
    assert template.spec.description == "AAOS head unit, phone, and body ECU"
    assert [m.name for m in template.spec.members] == ["headunit", "phone", "ecu"]
    assert template.spec.members[1].optional is True
    assert template.spec.members[2].exporter_ref.name == "ecu-2"
    assert template.spec.constraints[0].same_label == "bus"
    assert template.spec.defaults.duration == "4h0m0s"
    assert template.spec.defaults.shared_with == ["ci"]


def test_lease_template_dumps_what_the_cluster_stores():
    template = V1Alpha1LeaseTemplate.from_dict(IVI_TEMPLATE)

    assert template.model_dump(mode="json", by_alias=True)["spec"] == IVI_TEMPLATE["spec"]
    assert yaml.safe_load(template.dump_yaml())["spec"] == IVI_TEMPLATE["spec"]


def test_minimal_lease_template_prints_no_unset_fields():
    template = V1Alpha1LeaseTemplate.from_dict(MINIMAL_TEMPLATE)

    assert template.model_dump(mode="json", by_alias=True)["spec"] == MINIMAL_TEMPLATE["spec"]


def test_lease_template_row_marks_optional_members():
    table = MagicMock()
    with patch("jumpstarter_kubernetes.leasetemplates.time_since", return_value="5m"):
        V1Alpha1LeaseTemplate.from_dict(IVI_TEMPLATE).rich_add_rows(table)

    assert table.add_row.call_args[0] == (
        "ivi",
        "headunit, phone*, ecu",
        "4h0m0s",
        "AAOS head unit, phone, and body ECU",
        "5m",
    )


def test_lease_template_row_shortens_a_long_description():
    template = V1Alpha1LeaseTemplate.from_dict(IVI_TEMPLATE)
    template.spec.description = "word " * 100

    assert len(template.description_summary()) == 60
    assert template.description_summary().endswith("…")


def test_lease_template_list_from_dict():
    templates = V1Alpha1LeaseTemplateList.from_dict({"items": [IVI_TEMPLATE, MINIMAL_TEMPLATE]})
    names = []
    templates.rich_add_names(names)

    assert names == ["leasetemplate.jumpstarter.dev/ivi", "leasetemplate.jumpstarter.dev/single"]
    assert templates.model_dump(mode="json", by_alias=True)["kind"] == "LeaseTemplateList"


def make_api() -> LeaseTemplatesV1Alpha1Api:
    api = LeaseTemplatesV1Alpha1Api("lab")
    api.api = MagicMock()
    return api


@pytest.mark.anyio
async def test_list_lease_templates():
    api = make_api()
    api.api.list_namespaced_custom_object = AsyncMock(return_value={"items": [IVI_TEMPLATE]})

    templates = await api.list_lease_templates()

    assert [t.metadata.name for t in templates.items] == ["ivi"]
    api.api.list_namespaced_custom_object.assert_awaited_once_with(
        namespace="lab", group="jumpstarter.dev", plural="leasetemplates", version="v1alpha1"
    )


@pytest.mark.anyio
async def test_get_lease_template():
    api = make_api()
    api.api.get_namespaced_custom_object = AsyncMock(return_value=IVI_TEMPLATE)

    template = await api.get_lease_template("ivi")

    assert template.metadata.name == "ivi"
    api.api.get_namespaced_custom_object.assert_awaited_once_with(
        namespace="lab", group="jumpstarter.dev", plural="leasetemplates", version="v1alpha1", name="ivi"
    )


@pytest.mark.anyio
async def test_delete_lease_template():
    api = make_api()
    api.api.delete_namespaced_custom_object = AsyncMock()

    await api.delete_lease_template("ivi")

    api.api.delete_namespaced_custom_object.assert_awaited_once_with(
        namespace="lab", name="ivi", group="jumpstarter.dev", plural="leasetemplates", version="v1alpha1"
    )
