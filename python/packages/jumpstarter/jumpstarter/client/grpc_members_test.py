from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import grpc
import pytest
from google.protobuf import duration_pb2
from jumpstarter_protocol import client_pb2, kubernetes_pb2
from rich.console import Console
from rich.table import Table

from jumpstarter.client.exceptions import ControllerTooOldError, LeaseError
from jumpstarter.client.grpc import (
    ClientService,
    Lease,
    LeaseConstraint,
    LeaseList,
    LeaseMember,
    LeaseTemplate,
    LeaseTemplateList,
)
from jumpstarter.client.lease_test import MockAioRpcError

pytestmark = pytest.mark.anyio


def members_lease_pb(name="lease-9m2qd", template=None, statuses=None, conditions=()):
    lease = client_pb2.Lease(
        name=f"namespaces/default/leases/{name}",
        client="namespaces/default/clients/ci-runner",
        duration=duration_pb2.Duration(seconds=2700),
        members=[
            client_pb2.LeaseMember(name="phone", selector="device-type=phone", optional=True),
            client_pb2.LeaseMember(name="headunit", selector="device-type=headunit"),
        ],
        constraints=[client_pb2.LeaseConstraint(member_names=["phone", "headunit"], same_label="rf-domain")],
        conditions=list(conditions),
    )
    if template:
        lease.template.template = f"namespaces/default/leasetemplates/{template}"
        lease.template.generation = 3
    for status in (
        statuses
        if statuses is not None
        else [
            client_pb2.LeaseMemberStatus(
                name="phone",
                state=client_pb2.LEASE_MEMBER_STATE_BOUND,
                exporter="namespaces/default/exporters/rack3-phone-4",
            ),
            client_pb2.LeaseMemberStatus(
                name="headunit",
                state=client_pb2.LEASE_MEMBER_STATE_BOUND,
                exporter="namespaces/default/exporters/rack3-hu-2",
            ),
        ]
    ):
        lease.member_statuses.append(status)
    return lease


def template_pb(name="ivi"):
    return client_pb2.LeaseTemplate(
        name=f"namespaces/default/leasetemplates/{name}",
        description="AAOS head unit, phone, and body ECU on one CAN bus",
        docs=["https://wiki.example.com/labs/ivi"],
        image="https://wiki.example.com/labs/ivi.png",
        instructions="Set the head unit to on before you start the phone.\n",
        members=[
            client_pb2.LeaseTemplateMember(
                member=client_pb2.LeaseMember(name="headunit", selector="device-type=aaos-headunit"), available=2
            ),
            client_pb2.LeaseTemplateMember(
                member=client_pb2.LeaseMember(name="phone", selector="device-type=phone", optional=True), available=0
            ),
            client_pb2.LeaseTemplateMember(
                member=client_pb2.LeaseMember(name="ecu", exporter_name="ecu-11"), available=1
            ),
        ],
        constraints=[client_pb2.LeaseConstraint(member_names=["headunit", "ecu"], same_label="bus")],
        default_duration=duration_pb2.Duration(seconds=7200),
        default_shared_with=["lab-support"],
        generation=3,
    )


def render(model, **kwargs) -> str:
    table = Table(box=None)
    model.rich_add_columns(table, **kwargs)
    model.rich_add_rows(table, **kwargs)
    console = Console(width=200, record=True)
    console.print(table)
    return console.export_text()


def service(stub) -> ClientService:
    svc = ClientService(channel=Mock(), namespace="default")
    svc.stub = stub
    return svc


class TestMemberLeaseModel:
    def test_reads_members_statuses_and_template(self):
        lease = Lease.from_protobuf(members_lease_pb(template="ivi"))
        assert lease.is_member_form
        assert lease.exporter == "" and lease.selector == ""
        assert [m.name for m in lease.members] == ["phone", "headunit"]
        assert lease.members[0].optional and lease.members[0].selector == "device-type=phone"
        assert lease.constraints == [LeaseConstraint(member_names=["phone", "headunit"], same_label="rf-domain")]
        # the response has a resource name; the client shows the bare name
        assert lease.template is not None and lease.template.template == "ivi" and lease.template.generation == 3
        assert lease.member_exporters() == {"phone": "rack3-phone-4", "headunit": "rack3-hu-2"}
        assert lease.holds_exporter("rack3-hu-2") and not lease.holds_exporter("other")

    def test_role_without_status_is_pending(self):
        lease = Lease.from_protobuf(members_lease_pb(statuses=[]))
        assert lease.member_exporters() == {"phone": None, "headunit": None}
        assert "phone=(pending)" in render(lease)

    def test_lost_and_omitted_members(self):
        lease = Lease.from_protobuf(
            members_lease_pb(
                statuses=[
                    client_pb2.LeaseMemberStatus(
                        name="phone", state=client_pb2.LEASE_MEMBER_STATE_OMITTED, reason="Excluded"
                    ),
                    client_pb2.LeaseMemberStatus(
                        name="headunit",
                        state=client_pb2.LEASE_MEMBER_STATE_LOST,
                        exporter="namespaces/default/exporters/rack3-hu-2",
                        reason="Offline",
                        message="no heartbeat",
                    ),
                ],
                conditions=[
                    kubernetes_pb2.Condition(type="Ready", status="False", reason="MemberLost"),
                    kubernetes_pb2.Condition(type="Degraded", status="True", reason="MemberLost"),
                ],
            )
        )
        assert lease.member_exporters() == {"phone": None, "headunit": None}
        [lost] = lease.lost_members()
        assert lost.describe_loss() == "member 'headunit' lost its exporter rack3-hu-2 (Offline): no heartbeat"
        assert lease.get_status() == "Degraded"
        text = render(lease)
        assert "phone=(omitted)" in text and "headunit=rack3-hu-2(lost)" in text

    def test_table_has_one_row_with_one_line_for_each_role(self):
        text = render(Lease.from_protobuf(members_lease_pb()))
        assert "TEMPLATE" not in text
        assert "phone=rack3-phone-4," in text
        assert "headunit=rack3-hu-2" in text
        assert text.count("lease-9m2qd") == 1

    def test_table_shows_template_name(self):
        text = render(Lease.from_protobuf(members_lease_pb(template="ivi")))
        assert "TEMPLATE" in text and "ivi" in text

    def test_list_shows_template_column_only_when_a_lease_has_one(self):
        scalar = client_pb2.Lease(
            name="namespaces/default/leases/one",
            client="namespaces/default/clients/ci-runner",
            selector="board=rpi",
            exporter="namespaces/default/exporters/rpi-1",
            duration=duration_pb2.Duration(seconds=60),
        )
        only_scalar = LeaseList(leases=[Lease.from_protobuf(scalar)], next_page_token=None)
        assert "TEMPLATE" not in render(only_scalar)
        mixed = LeaseList(
            leases=[Lease.from_protobuf(scalar), Lease.from_protobuf(members_lease_pb(template="ivi"))],
            next_page_token=None,
        )
        text = render(mixed)
        assert "TEMPLATE" in text and "rpi-1" in text and "headunit=rack3-hu-2" in text

    def test_json_has_members_and_scalar_json_does_not(self):
        data = Lease.from_protobuf(members_lease_pb(template="ivi")).model_dump(mode="json")
        assert data["template"] == {"template": "ivi", "generation": 3}
        assert data["member_statuses"][0]["exporter"] == "rack3-phone-4"
        assert data["member_statuses"][0]["state"] == "Bound"
        assert data["members"][1]["name"] == "headunit"
        scalar = Lease.from_protobuf(
            client_pb2.Lease(
                name="namespaces/default/leases/one",
                client="namespaces/default/clients/c",
                selector="a=b",
                duration=duration_pb2.Duration(seconds=60),
            )
        ).model_dump(mode="json")
        assert not {"members", "constraints", "template", "member_statuses"} & set(scalar)

    def test_filter_by_selector_reads_member_selectors(self):
        leases = LeaseList(leases=[Lease.from_protobuf(members_lease_pb())], next_page_token=None)
        assert len(leases.filter_by_selector("device-type=phone").leases) == 1
        assert len(leases.filter_by_selector("device-type=ecu").leases) == 0


class TestLeaseTemplateModel:
    def test_from_protobuf(self):
        template = LeaseTemplate.from_protobuf(template_pb())
        assert template.name == "ivi" and template.namespace == "default"
        assert template.role_names() == ["headunit", "phone", "ecu"]
        phone, ecu = template.member("phone"), template.member("ecu")
        assert phone is not None and phone.optional
        assert ecu is not None and ecu.exporter_name == "ecu-11" and ecu.selector is None
        assert template.default_duration == timedelta(hours=2)
        assert template.default_shared_with == ["lab-support"]
        assert template.available_summary() == "2/3"

    def test_table(self):
        templates = LeaseTemplateList.from_protobuf(
            client_pb2.ListLeaseTemplatesResponse(lease_templates=[template_pb()])
        )
        text = render(templates)
        for column in ("NAME", "MEMBERS", "AVAILABLE", "DESCRIPTION"):
            assert column in text
        assert "headunit, phone*, ecu" in text
        assert "2/3" in text
        names = []
        templates.rich_add_names(names)
        assert names == ["ivi"]

    def test_long_description_is_truncated(self):
        pb = template_pb()
        pb.description = "x" * 300
        text = render(LeaseTemplate.from_protobuf(pb))
        assert "x" * 61 not in text and "…" in text


class TestClientServiceTemplates:
    async def test_get_lease_template(self):
        stub = Mock(GetLeaseTemplate=AsyncMock(return_value=template_pb()))
        template = await service(stub).GetLeaseTemplate(name="ivi")
        assert template.name == "ivi"
        assert stub.GetLeaseTemplate.call_args.args[0].name == "namespaces/default/leasetemplates/ivi"

    async def test_get_lease_template_not_found(self):
        stub = Mock(GetLeaseTemplate=AsyncMock(side_effect=MockAioRpcError(grpc.StatusCode.NOT_FOUND, "nope")))
        with pytest.raises(LeaseError, match="lease template 'ivi' not found"):
            await service(stub).GetLeaseTemplate(name="ivi")

    async def test_list_lease_templates(self):
        stub = Mock(
            ListLeaseTemplates=AsyncMock(
                return_value=client_pb2.ListLeaseTemplatesResponse(lease_templates=[template_pb()], next_page_token="n")
            )
        )
        templates = await service(stub).ListLeaseTemplates(page_size=10)
        assert [t.name for t in templates.lease_templates] == ["ivi"]
        assert templates.next_page_token == "n"
        assert stub.ListLeaseTemplates.call_args.args[0].parent == "namespaces/default"

    @pytest.mark.parametrize("rpc", ["GetLeaseTemplate", "ListLeaseTemplates"])
    async def test_old_controller_has_no_template_rpcs(self, rpc):
        stub = Mock(**{rpc: AsyncMock(side_effect=MockAioRpcError(grpc.StatusCode.UNIMPLEMENTED, "unknown method"))})
        svc = service(stub)
        with pytest.raises(ControllerTooOldError, match="controller is too old for lease templates"):
            if rpc == "GetLeaseTemplate":
                await svc.GetLeaseTemplate(name="ivi")
            else:
                await svc.ListLeaseTemplates()


class TestCreateLeaseForms:
    def _stub(self):
        return Mock(CreateLease=AsyncMock(return_value=members_lease_pb()))

    async def test_scalar_form_sets_no_member_fields(self):
        stub = self._stub()
        await service(stub).CreateLease(selector="a=b", duration=timedelta(minutes=5))
        lease = stub.CreateLease.call_args.args[0].lease
        assert lease.selector == "a=b"
        assert not lease.members and not lease.constraints and not lease.HasField("template")

    async def test_template_form(self):
        stub = self._stub()
        await service(stub).CreateLease(
            duration=None,
            template="ivi",
            excluded_members=["phone"],
            member_selectors={"headunit": "soc=sa8775p"},
            shared_with=["lab-support"],
        )
        lease = stub.CreateLease.call_args.args[0].lease
        assert lease.template.template == "ivi"
        assert list(lease.template.excluded_members) == ["phone"]
        assert dict(lease.template.member_selectors) == {"headunit": "soc=sa8775p"}
        assert lease.selector == "" and lease.exporter_name == "" and not lease.members
        # no duration: the default of the template applies
        assert not lease.HasField("duration")

    async def test_members_form(self):
        stub = self._stub()
        await service(stub).CreateLease(
            duration=timedelta(minutes=45),
            members=[
                LeaseMember(name="phone", selector="device-type=phone"),
                LeaseMember(name="ecu", exporter_name="ecu-11", optional=True),
            ],
            constraints=[LeaseConstraint(member_names=["phone", "ecu"], same_label="bus")],
        )
        lease = stub.CreateLease.call_args.args[0].lease
        assert [(m.name, m.WhichOneof("selection")) for m in lease.members] == [
            ("phone", "selector"),
            ("ecu", "exporter_name"),
        ]
        assert lease.members[1].optional and not any(m.excluded for m in lease.members)
        assert lease.constraints[0].same_label == "bus"
        assert not lease.HasField("template") and lease.selector == ""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"selector": "a=b", "template": "ivi"},
            {"exporter_name": "e", "members": [LeaseMember(name="a", selector="x=y")]},
            {"template": "ivi", "members": [LeaseMember(name="a", selector="x=y")]},
            {"selector": "a=b", "excluded_members": ["phone"]},
            {"selector": "a=b", "member_selectors": {"phone": "x=y"}},
            {"selector": "a=b", "constraints": [LeaseConstraint(member_names=["a", "b"], same_label="k")]},
        ],
    )
    async def test_mixed_forms_are_rejected_before_the_request(self, kwargs):
        stub = self._stub()
        with pytest.raises(ValueError):
            await service(stub).CreateLease(duration=timedelta(minutes=5), **kwargs)
        stub.CreateLease.assert_not_called()

    async def test_duration_is_required_without_a_template(self):
        with pytest.raises(ValueError, match="duration is required"):
            await service(self._stub()).CreateLease(selector="a=b", duration=None)

    @pytest.mark.parametrize(
        "kwargs",
        [{"template": "ivi"}, {"members": [LeaseMember(name="a", selector="x=y")]}],
    )
    async def test_old_controller_rejects_a_request_without_selector(self, kwargs):
        stub = Mock(
            CreateLease=AsyncMock(
                side_effect=MockAioRpcError(
                    grpc.StatusCode.INVALID_ARGUMENT, "one of selector or exporter_name is required"
                )
            )
        )
        with pytest.raises(ControllerTooOldError, match="controller is too old for leases with members"):
            await service(stub).CreateLease(duration=timedelta(minutes=5), **kwargs)

    async def test_other_rejections_keep_the_reason_of_the_controller(self):
        stub = Mock(
            CreateLease=AsyncMock(
                side_effect=MockAioRpcError(grpc.StatusCode.INVALID_ARGUMENT, "member phone is not optional")
            )
        )
        with pytest.raises(LeaseError, match="member phone is not optional") as excinfo:
            await service(stub).CreateLease(duration=None, template="ivi", excluded_members=["phone"])
        assert not isinstance(excinfo.value, ControllerTooOldError)
