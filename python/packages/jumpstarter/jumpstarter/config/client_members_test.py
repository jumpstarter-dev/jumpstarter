from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_power.client import PowerClient

from jumpstarter.client.grpc import LeaseList, LeaseTemplateList
from jumpstarter.client.grpc_members_test import members_lease_pb, template_pb
from jumpstarter.client.lease import Lease, LeaseMembers, MembersLease
from jumpstarter.config.client import ClientConfigV1Alpha1, ClientConfigV1Alpha1Drivers
from jumpstarter.config.common import ObjectMeta
from jumpstarter.config.env import JMP_LEASE


class Headunit(CompositeClient):
    power: PowerClient


class Projection(LeaseMembers, template="projection"):
    headunit: Headunit


@pytest.fixture
def config(monkeypatch):
    monkeypatch.delenv(JMP_LEASE, raising=False)
    return ClientConfigV1Alpha1(
        alias="testclient",
        metadata=ObjectMeta(namespace="default", name="testclient"),
        endpoint="jumpstarter.my-lab.com:1443",
        token="token",
        drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=True),
    )


@pytest.fixture
def created(config):
    """The lease objects that config.lease() builds, without the request to a controller."""
    leases = []

    @asynccontextmanager
    async def enter(self):
        leases.append(self)
        yield self

    with (
        patch.object(ClientConfigV1Alpha1, "channel", AsyncMock(return_value=Mock())),
        patch.object(Lease, "__asynccontextmanager__", enter),
    ):
        yield leases


def test_members_class_gives_a_members_lease_of_that_class(config, created):
    with config.lease(Projection, duration=timedelta(minutes=45), member_selectors={"headunit": "soc=x"}) as lease:
        assert isinstance(lease, MembersLease)
        assert lease.members_class is Projection
        assert lease.template is None  # the class names it
        assert lease.duration == timedelta(minutes=45)
        assert lease.member_selectors == {"headunit": "soc=x"}
        assert lease.release and not lease.name


def test_template_name_gives_a_members_lease_of_plain_lease_members(config, created):
    with config.lease(template="ivi") as lease:
        assert isinstance(lease, MembersLease)
        assert lease.members_class is LeaseMembers
        assert lease.template == "ivi"
        assert lease.duration is None  # the default of the template


@pytest.mark.parametrize("call", [lambda c: c.lease("ivi"), lambda c: c.lease(selector="ivi")])
def test_a_string_is_a_selector_and_never_a_template_name(config, created, call):
    with call(config) as lease:
        assert type(lease) is Lease
        assert lease.selector == "ivi"
        assert lease.duration == timedelta(minutes=30)


def test_lease_name_attaches_with_a_members_class(config, created):
    with config.lease(Projection, lease_name="lease-9m2qd") as lease:
        assert isinstance(lease, MembersLease)
        assert lease.name == "lease-9m2qd"
        assert not lease.release


def test_jmp_lease_attaches_as_for_a_scalar_lease(config, created, monkeypatch):
    monkeypatch.setenv(JMP_LEASE, "from-env")
    with config.lease(Projection) as lease:
        assert lease.name == "from-env" and not lease.release


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda c: c.lease("a=b", template="ivi"), "a selector cannot be used with template="),
        (lambda c: c.lease(Projection, exporter_name="e"), "exporter_name cannot be used with a lease template"),
        (lambda c: c.lease(Projection, template="other"), "declared for the template 'projection', not 'other'"),
        (lambda c: c.lease("a=b", member_selectors={"x": "y=z"}), "member_selectors needs a lease template"),
    ],
)
def test_mixed_forms_are_rejected(config, created, call, message):
    with pytest.raises(ValueError, match=message), call(config):
        pass
    assert created == []


@pytest.mark.anyio
async def test_create_lease_passes_member_arguments_only_when_set(config):
    service = Mock(CreateLease=AsyncMock(return_value="lease"))
    with (
        patch.object(ClientConfigV1Alpha1, "channel", AsyncMock(return_value=Mock())),
        patch("jumpstarter.config.client.ClientService", return_value=service),
    ):
        await config.create_lease(duration=None, template="ivi", excluded_members=["phone"])
    kwargs = service.CreateLease.call_args.kwargs
    assert kwargs["template"] == "ivi" and kwargs["excluded_members"] == ["phone"]
    assert "members" not in kwargs and "member_selectors" not in kwargs


@pytest.mark.anyio
async def test_list_lease_templates_reads_every_page(config):
    from jumpstarter.client.grpc import LeaseTemplate

    first = LeaseTemplateList(lease_templates=[LeaseTemplate.from_protobuf(template_pb("a"))], next_page_token="t")
    second = LeaseTemplateList(lease_templates=[LeaseTemplate.from_protobuf(template_pb("b"))], next_page_token="")
    service = Mock(ListLeaseTemplates=AsyncMock(side_effect=[first, second]))
    with (
        patch.object(ClientConfigV1Alpha1, "channel", AsyncMock(return_value=Mock())),
        patch("jumpstarter.config.client.ClientService", return_value=service),
    ):
        templates = await config.list_lease_templates(page_size=1)
    assert [t.name for t in templates.lease_templates] == ["a", "b"]
    assert service.ListLeaseTemplates.call_args_list[1].kwargs == {"page_size": 1, "page_token": "t"}


@pytest.mark.anyio
async def test_exporter_listing_shows_the_lease_of_each_bound_member(config):
    from jumpstarter_protocol import kubernetes_pb2

    from jumpstarter.client.grpc import Exporter, ExporterList
    from jumpstarter.client.grpc import Lease as LeaseModel

    lease_pb = members_lease_pb(conditions=[kubernetes_pb2.Condition(type="Ready", status="True")])
    lease_pb.effective_begin_time.GetCurrentTime()
    lease = LeaseModel.from_protobuf(lease_pb)
    exporters = ExporterList(
        exporters=[
            Exporter(namespace="default", name="rack3-hu-2", labels={}),
            Exporter(namespace="default", name="free", labels={}),
        ],
        next_page_token=None,
    )
    service = Mock(
        ListExporters=AsyncMock(return_value=exporters),
        ListLeases=AsyncMock(return_value=LeaseList(leases=[lease], next_page_token=None)),
    )
    with (
        patch.object(ClientConfigV1Alpha1, "channel", AsyncMock(return_value=Mock())),
        patch("jumpstarter.config.client.ClientService", return_value=service),
    ):
        result = await config.list_exporters(include_leases=True)
    assert result.exporters[0].lease is lease
    assert result.exporters[1].lease is None
