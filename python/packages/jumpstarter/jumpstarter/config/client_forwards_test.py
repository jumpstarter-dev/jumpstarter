"""Forwards and ports in the client config API (JEP-0016)."""

from datetime import timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jumpstarter.client._members_testing import exporter_pb, forward_pb
from jumpstarter.client.grpc import ExporterList, LeaseForward, LeaseMember
from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter.config.common import ObjectMeta

pytestmark = pytest.mark.anyio

MEMBERS = [LeaseMember(name="phone", selector="a=b"), LeaseMember(name="headunit", selector="c=d")]
FORWARDS = [LeaseForward.from_protobuf(forward_pb())]


def client_config() -> ClientConfigV1Alpha1:
    return ClientConfigV1Alpha1(
        alias="testclient",
        metadata=ObjectMeta(namespace="default", name="testclient"),
        endpoint="jumpstarter.example.com",
        token="dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz",
    )


def patched_service(**methods):
    service = Mock(**methods)
    return (
        patch.object(ClientConfigV1Alpha1, "channel", AsyncMock(return_value=Mock())),
        patch("jumpstarter.config.client.ClientService", return_value=service),
        service,
    )


async def test_create_lease_passes_the_forwards_of_ad_hoc_members():
    channel, cls, service = patched_service(CreateLease=AsyncMock(return_value="lease"))
    with channel, cls:
        await client_config().create_lease(duration=timedelta(minutes=5), members=MEMBERS, forwards=FORWARDS)
    kwargs = service.CreateLease.call_args.kwargs
    assert kwargs["forwards"] == FORWARDS and kwargs["members"] == MEMBERS


async def test_create_lease_without_forwards_is_the_request_of_before():
    channel, cls, service = patched_service(CreateLease=AsyncMock(return_value="lease"))
    with channel, cls:
        await client_config().create_lease(duration=timedelta(minutes=5), members=MEMBERS)
    assert "forwards" not in service.CreateLease.call_args.kwargs


async def test_list_exporters_can_include_the_ports():
    page = ExporterList.from_protobuf(
        Mock(exporters=[exporter_pb(ports=[("rootcanal", "provides")])], next_page_token="")
    )
    channel, cls, _ = patched_service(ListExporters=AsyncMock(return_value=page))
    with channel, cls:
        with_ports = await client_config().list_exporters(include_ports=True)
        without = await client_config().list_exporters()
    assert with_ports.include_ports and with_ports.model_dump()["exporters"][0]["ports"][0]["name"] == "rootcanal"
    assert not without.include_ports and "ports" not in without.model_dump()["exporters"][0]


async def test_lease_members_async_passes_forwards_and_the_tolerance_of_the_shell():
    seen = {}

    class FakeLease:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    with (
        patch.object(ClientConfigV1Alpha1, "channel", AsyncMock(return_value=Mock())),
        patch("jumpstarter.client.lease.MembersLease", FakeLease),
    ):
        async with client_config().lease_members_async(
            portal=Mock(), members=MEMBERS, forwards=FORWARDS, tolerate_failed_forwards=True
        ):
            pass
        assert seen["requested_forwards"] == FORWARDS and seen["tolerate_failed_forwards"] is True
        async with client_config().lease_members_async(portal=Mock(), template="ivi"):
            pass
        assert seen["requested_forwards"] == [] and seen["tolerate_failed_forwards"] is False
