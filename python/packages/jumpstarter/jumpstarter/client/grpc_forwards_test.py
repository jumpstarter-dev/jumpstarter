"""Models and RPC wrappers of forwards and ports (JEP-0016)."""

import json
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import grpc
import pytest
import yaml
from google.protobuf import duration_pb2
from jumpstarter_protocol import client_pb2
from rich.console import Console
from rich.table import Table

from jumpstarter.client._members_testing import (
    exporter_pb,
    forward_end_status_pb,
    forward_pb,
    forward_status_pb,
    members_lease_pb,
    template_pb,
)
from jumpstarter.client.exceptions import ControllerTooOldError, LeaseError
from jumpstarter.client.grpc import (
    ClientService,
    Exporter,
    ExporterList,
    ForwardEndpoint,
    Lease,
    LeaseForward,
    LeaseForwardEndStatus,
    LeaseForwardStatus,
    LeaseList,
    LeaseMember,
    LeaseTemplate,
    LeaseTemplateList,
    PortReport,
    validate_forwards,
)
from jumpstarter.client.lease_test import MockAioRpcError

pytestmark = pytest.mark.anyio

MEMBERS = [LeaseMember(name="peer", selector="device-type=bt-peer"), LeaseMember(name="headunit", selector="a=b")]


def render(model, **kwargs) -> str:
    table = Table(box=None)
    model.rich_add_columns(table, **kwargs)
    model.rich_add_rows(table, **kwargs)
    console = Console(width=240, record=True)
    console.print(table)
    return console.export_text()


def service(stub) -> ClientService:
    svc = ClientService(channel=Mock(), namespace="default")
    svc.stub = stub
    return svc


def forward(name="bt", *endpoints) -> LeaseForward:
    return LeaseForward.from_protobuf(forward_pb(name, *(endpoints or ("headunit.rootcanal", "peer.controller"))))


def scalar_lease_pb():
    return client_pb2.Lease(
        name="namespaces/default/leases/lease-1",
        client="namespaces/default/clients/ci-runner",
        exporter="namespaces/default/exporters/rack3-hu-2",
        selector="a=b",
        duration=duration_pb2.Duration(seconds=60),
    )


class TestForwardModels:
    def test_forward_round_trip(self):
        model = LeaseForward.from_protobuf(forward_pb("console", "dut.console", "logger.console", "debugger.console"))
        assert model.name == "console"
        assert model.endpoints == [
            ForwardEndpoint(member_name="dut", port_name="console"),
            ForwardEndpoint(member_name="logger", port_name="console"),
            ForwardEndpoint(member_name="debugger", port_name="console"),
        ]
        assert model.to_protobuf() == forward_pb("console", "dut.console", "logger.console", "debugger.console")
        assert str(model.endpoints[0]) == "dut.console"

    @pytest.mark.parametrize("state", ["Pending", "Disabled", "Listening", "Connected", "Failed"])
    def test_each_state_has_its_name(self, state):
        status = LeaseForwardStatus.from_protobuf(forward_status_pb("bt", state))
        assert status.state == state and status.message is None
        assert status.usable == (state in ("Listening", "Connected"))
        assert status.failed == (state == "Failed")

    def test_unspecified_state_is_unknown(self):
        assert LeaseForwardStatus.from_protobuf(client_pb2.LeaseForwardStatus(name="bt")).state == "Unknown"

    @pytest.mark.parametrize("state", ["Pending", "Ready", "Connected", "Failed"])
    def test_each_end_state_has_its_name(self, state):
        end = LeaseForwardEndStatus.from_protobuf(forward_end_status_pb("headunit", state))
        assert (end.member_name, end.state, end.message) == ("headunit", state, None)
        assert end.describe() == f"headunit: {state}"

    def test_unspecified_end_state_is_unknown(self):
        end = LeaseForwardEndStatus.from_protobuf(client_pb2.LeaseForwardEndStatus(member_name="headunit"))
        assert end.state == "Unknown"

    def test_status_has_the_state_and_the_message_of_each_end(self):
        status = LeaseForwardStatus.from_protobuf(
            forward_status_pb(
                "bt",
                "Failed",
                "The setup failed on role `phone`: address already in use",
                ends=[("headunit", "Ready"), ("phone", "Failed", "address already in use")],
            )
        )
        assert status.ends == [
            LeaseForwardEndStatus(member_name="headunit", state="Ready"),
            LeaseForwardEndStatus(member_name="phone", state="Failed", message="address already in use"),
        ]
        phone = status.end("phone")
        assert phone is not None and phone.state == "Failed" and status.end("tablet") is None
        assert status.describe_ends() == "headunit: Ready\nphone: Failed (address already in use)"
        assert status.describe_ends("; ") == "headunit: Ready; phone: Failed (address already in use)"

    @pytest.mark.parametrize("state", ["Listening", "Connected"])
    def test_ends_of_a_usable_forward_are_not_described(self, state):
        status = LeaseForwardStatus.from_protobuf(
            forward_status_pb("bt", state, ends=[("headunit", "Ready"), ("phone", "Connected")])
        )
        assert len(status.ends) == 2 and status.describe_ends() == ""

    def test_status_of_a_controller_without_ends(self):
        status = LeaseForwardStatus.from_protobuf(forward_status_pb("bt", "Pending"))
        assert status.ends == [] and status.describe_ends() == ""
        assert "ends" not in status.model_dump(mode="json")

    def test_failure_text_has_the_forward_and_the_cause(self):
        failed = LeaseForwardStatus.from_protobuf(
            forward_status_pb("bt", "Failed", "port 'rootcanal' is not on exporter rack3-hu-2")
        )
        assert failed.describe_failure() == "forward 'bt' failed: port 'rootcanal' is not on exporter rack3-hu-2"
        disabled = LeaseForwardStatus(name="bt", state="Disabled", message="member 'phone' is omitted")
        assert disabled.describe_failure() == "forward 'bt' is disabled: member 'phone' is omitted"

    def test_arrow_points_from_provides_to_each_requires(self):
        directions = {("dut", "console"): "provides", ("logger", "console"): "requires", ("dbg", "console"): "requires"}
        # the endpoints can be in any sequence: the direction comes from the exporter reports
        model = forward("console", "logger.console", "dut.console", "dbg.console")
        assert model.describe(directions) == "dut.console → logger.console, dbg.console"

    def test_double_arrow_connects_the_two_ends_of_a_path(self):
        directions = {("dut", "uart"): "connects", ("companion", "uart"): "connects"}
        assert forward("uart", "dut.uart", "companion.uart").describe(directions) == "dut.uart ↔ companion.uart"

    @pytest.mark.parametrize(
        "directions",
        [
            None,
            {("headunit", "rootcanal"): "provides"},  # one direction is not known
            {("headunit", "rootcanal"): "provides", ("peer", "controller"): "provides"},  # an invalid forward
        ],
    )
    def test_without_usable_directions_the_endpoints_are_listed(self, directions):
        assert forward().describe(directions) == "headunit.rootcanal, peer.controller"


class TestLeaseWithForwards:
    def _lease(self, state="Listening", message=None):
        return Lease.from_protobuf(
            members_lease_pb(
                forwards=[forward_pb(), forward_pb("can", "headunit.can", "phone.can-remote")],
                forward_statuses=[forward_status_pb("bt", state, message)],
            )
        )

    def test_reads_forwards_and_statuses(self):
        lease = self._lease("Failed", "the listen address is in use")
        assert [f.name for f in lease.forwards] == ["bt", "can"]
        bt = lease.forward("bt")
        assert bt is not None and [str(e) for e in bt.endpoints] == ["headunit.rootcanal", "phone.controller"]
        assert lease.forward("other") is None
        assert lease.forward_state("bt") == "Failed"
        status = lease.forward_status("bt")
        assert status is not None and status.message == "the listen address is in use"
        # a forward that the controller did not report yet
        assert lease.forward_status("can") is None and lease.forward_state("can") == "Pending"
        assert [s.name for s in lease.failed_forwards()] == ["bt"]

    def test_table_has_the_state_of_each_forward(self):
        text = render(self._lease())
        assert "FORWARDS" in text
        assert "bt=Listening" in text and "can=Pending" in text

    def test_list_shows_the_column_only_when_a_lease_has_forwards(self):
        with_forwards = LeaseList(leases=[self._lease(), Lease.from_protobuf(scalar_lease_pb())], next_page_token=None)
        text = render(with_forwards)
        assert "FORWARDS" in text and "bt=Listening" in text
        without = LeaseList(leases=[Lease.from_protobuf(members_lease_pb())], next_page_token=None)
        assert "FORWARDS" not in render(without)
        # the class alone still gives the columns of before forwards
        table = Table(box=None)
        Lease.rich_add_columns(table)
        assert "FORWARDS" not in [column.header for column in table.columns]

    def test_json_and_yaml_have_forwards_and_statuses(self):
        data = self._lease("Failed", "no report in 60s").model_dump(mode="json")
        assert data["forwards"][0] == {
            "name": "bt",
            "endpoints": [
                {"member_name": "headunit", "port_name": "rootcanal"},
                {"member_name": "phone", "port_name": "controller"},
            ],
        }
        assert data["forward_statuses"] == [{"name": "bt", "state": "Failed", "message": "no report in 60s"}]
        json.dumps(data)

    def test_json_and_yaml_have_the_ends_of_each_forward(self):
        lease = Lease.from_protobuf(
            members_lease_pb(
                forwards=[forward_pb()],
                forward_statuses=[
                    forward_status_pb(
                        "bt", "Pending", ends=[("headunit", "Ready"), ("phone", "Failed", "address already in use")]
                    )
                ],
            )
        )
        data = lease.model_dump(mode="json")
        assert data["forward_statuses"] == [
            {
                "name": "bt",
                "state": "Pending",
                "message": None,
                "ends": [
                    {"member_name": "headunit", "state": "Ready", "message": None},
                    {"member_name": "phone", "state": "Failed", "message": "address already in use"},
                ],
            }
        ]
        assert yaml.safe_load(yaml.safe_dump(data)) == json.loads(json.dumps(data))

    def test_lease_without_forwards_prints_as_before(self):
        members = Lease.from_protobuf(members_lease_pb()).model_dump(mode="json")
        scalar = Lease.from_protobuf(scalar_lease_pb()).model_dump(mode="json")
        for data in (members, scalar):
            assert "forwards" not in data and "forward_statuses" not in data
        assert "members" not in scalar


class TestTemplateWithForwards:
    def test_reads_forwards(self):
        template = LeaseTemplate.from_protobuf(
            template_pb(forwards=[forward_pb("bt", "headunit.rootcanal", "phone.c")])
        )
        assert [f.name for f in template.forwards] == ["bt"]
        assert template.forward("bt") is not None and template.forward("other") is None
        assert template.model_dump(mode="json")["forwards"][0]["endpoints"][1] == {
            "member_name": "phone",
            "port_name": "c",
        }

    def test_template_without_forwards_prints_as_before(self):
        template = LeaseTemplate.from_protobuf(template_pb())
        assert template.forwards == [] and "forwards" not in template.model_dump(mode="json")
        assert "FORWARDS" not in render(template)

    def test_list_shows_the_forwards_of_each_template(self):
        templates = LeaseTemplateList(
            lease_templates=[
                LeaseTemplate.from_protobuf(template_pb("ivi", forwards=[forward_pb("bt"), forward_pb("can")])),
                LeaseTemplate.from_protobuf(template_pb("bench")),
            ],
            next_page_token=None,
        )
        text = render(templates)
        assert "FORWARDS" in text and "bt, can" in text
        plain = LeaseTemplateList(lease_templates=[LeaseTemplate.from_protobuf(template_pb())], next_page_token=None)
        assert "FORWARDS" not in render(plain)


class TestExporterPorts:
    PORTS = (
        ("rootcanal", "provides", "hci-h4"),
        ("can0", "connects", None, "wired"),
        ("someip-sd", "connects", None, "socket", "datagram"),
        ("controller", "requires"),
    )

    def test_reads_ports(self):
        exporter = Exporter.from_protobuf(exporter_pb(ports=self.PORTS))
        assert exporter.ports == [
            PortReport(name="rootcanal", direction="provides", protocol="hci-h4"),
            PortReport(name="can0", direction="connects", attachment="wired"),
            PortReport(name="someip-sd", direction="connects", transport="datagram"),
            PortReport(name="controller", direction="requires"),
        ]
        rootcanal = exporter.port("rootcanal")
        assert rootcanal is not None and rootcanal.forwardable
        assert exporter.port("missing") is None
        # a forward accepts only a socket that carries a stream (DD-8, DD-9)
        assert [port.forwardable for port in exporter.ports] == [True, False, False, True]

    def test_unspecified_attachment_and_transport_are_socket_and_stream(self):
        port = Exporter.from_protobuf(exporter_pb(ports=[("p", "provides")])).ports[0]
        assert (port.attachment, port.transport, port.protocol) == ("socket", "stream", None)

    def test_port_prints_as_its_declaration(self):
        ports = Exporter.from_protobuf(exporter_pb(ports=self.PORTS)).model_dump(mode="json")["ports"]
        assert ports == [
            {"name": "rootcanal", "direction": "provides", "protocol": "hci-h4"},
            {"name": "can0", "direction": "connects", "attachment": "wired"},
            {"name": "someip-sd", "direction": "connects", "transport": "datagram"},
            {"name": "controller", "direction": "requires"},
        ]

    def test_exporter_without_ports_prints_as_before(self):
        exporter = Exporter.from_protobuf(exporter_pb())
        assert exporter.ports == [] and "ports" not in exporter.model_dump(mode="json")

    def _list(self, include_ports):
        exporters = ExporterList(
            exporters=[
                Exporter.from_protobuf(exporter_pb("rack3-hu-2", ports=self.PORTS)),
                Exporter.from_protobuf(exporter_pb("old-exporter")),
            ],
            next_page_token=None,
        )
        exporters.include_ports = include_ports
        return exporters

    def test_ports_are_shown_only_when_asked_for(self):
        assert "PORTS" not in render(self._list(False))
        data = json.loads(self._list(False).model_dump_json())
        assert all("ports" not in exporter for exporter in data["exporters"])
        assert all("ports" not in exporter for exporter in self._list(False).model_dump()["exporters"])

    def test_with_ports_table(self):
        text = render(self._list(True))
        assert "PORTS" in text
        assert "rootcanal (provides, hci-h4)" in text
        assert "can0 (connects, wired)" in text
        assert "someip-sd (connects, datagram)" in text
        assert "controller (requires)" in text

    def test_with_ports_json_as_in_the_jep(self):
        data = json.loads(self._list(True).model_dump_json())
        assert data["exporters"][0]["ports"][0] == {"name": "rootcanal", "direction": "provides", "protocol": "hci-h4"}
        # an exporter that reports no ports says so
        assert data["exporters"][1]["ports"] == []
        assert self._list(True).model_dump(mode="json")["exporters"][1]["ports"] == []

    async def test_get_and_list_exporters_read_the_ports(self):
        stub = Mock(
            GetExporter=AsyncMock(return_value=exporter_pb(ports=self.PORTS)),
            ListExporters=AsyncMock(
                return_value=client_pb2.ListExportersResponse(exporters=[exporter_pb(ports=self.PORTS)])
            ),
        )
        assert len((await service(stub).GetExporter(name="rack3-hu-2")).ports) == 4
        assert (await service(stub).ListExporters()).exporters[0].ports[0].name == "rootcanal"


class TestValidateForwards:
    def test_valid_forwards(self):
        validate_forwards([forward(), forward("uart", "peer.uart", "headunit.uart")], ["peer", "headunit"])

    @pytest.mark.parametrize(
        "forwards,message",
        [
            ([forward("Bad_Name")], "invalid forward name 'Bad_Name'"),
            ([forward("b" * 64)], "invalid forward name"),
            ([forward(), forward()], "the forward 'bt' is given more than once"),
            ([forward("peer")], "'peer' is a member: a forward and a member cannot have the same name"),
            ([forward("bt", "headunit.rootcanal")], "has 1 endpoints: a forward connects 2 to 8 endpoints"),
            ([forward("bt", "headunit.rootcanal", "tablet.controller")], "names the member 'tablet'"),
            ([forward("bt", "headunit.rootcanal", "headunit.controller")], "two endpoints on the member 'headunit'"),
            ([forward("bt", "headunit.Root", "peer.controller")], "invalid port name 'Root'"),
            ([forward(f"f{i}") for i in range(17)], "too many forwards \\(max 16\\)"),
        ],
    )
    def test_rejected(self, forwards, message):
        with pytest.raises(ValueError, match=message):
            validate_forwards(forwards, ["peer", "headunit"])

    def test_more_than_eight_endpoints(self):
        roles = [f"m{i}" for i in range(9)]
        with pytest.raises(ValueError, match="has 9 endpoints"):
            validate_forwards([forward("bus", *(f"{role}.can" for role in roles))], roles)


class TestCreateLeaseWithForwards:
    def _stub(self, response=None):
        return Mock(
            CreateLease=AsyncMock(return_value=response or members_lease_pb(forwards=[forward_pb()])),
            DeleteLease=AsyncMock(),
        )

    async def test_members_request_sends_the_forwards(self):
        stub = self._stub()
        created = await service(stub).CreateLease(duration=timedelta(minutes=45), members=MEMBERS, forwards=[forward()])
        lease = stub.CreateLease.call_args.args[0].lease
        assert lease.forwards == [forward_pb("bt", "headunit.rootcanal", "peer.controller")]
        assert [m.name for m in lease.members] == ["peer", "headunit"]
        assert [f.name for f in created.forwards] == ["bt"]
        stub.DeleteLease.assert_not_called()

    @pytest.mark.parametrize(
        "kwargs,message",
        [
            ({"selector": "a=b"}, "forwards need members"),
            ({"exporter_name": "rack3-hu-2"}, "forwards need members"),
            ({"template": "ivi"}, "forwards cannot be used with a template: the template has the forwards"),
        ],
    )
    async def test_forwards_in_a_scalar_or_a_template_request_are_rejected_before_the_request(self, kwargs, message):
        stub = self._stub()
        with pytest.raises(ValueError, match=message):
            await service(stub).CreateLease(duration=timedelta(minutes=5), forwards=[forward()], **kwargs)
        stub.CreateLease.assert_not_called()

    async def test_invalid_forward_is_rejected_before_the_request(self):
        stub = self._stub()
        with pytest.raises(ValueError, match="names the member 'tablet'"):
            await service(stub).CreateLease(
                duration=timedelta(minutes=5),
                members=MEMBERS,
                forwards=[forward("bt", "headunit.rootcanal", "tablet.controller")],
            )
        stub.CreateLease.assert_not_called()

    async def test_controller_without_forwards_leaves_no_lease(self):
        # A controller with JEP-0015 and without JEP-0016 ignores the forwards and creates the lease.
        stub = self._stub(members_lease_pb(name="lease-old"))
        with pytest.raises(ControllerTooOldError, match="controller is too old for port forwards"):
            await service(stub).CreateLease(duration=timedelta(minutes=45), members=MEMBERS, forwards=[forward()])
        request = stub.DeleteLease.call_args.args[0]
        assert request.name == "namespaces/default/leases/lease-old"

    async def test_too_old_is_still_reported_when_the_delete_fails(self, caplog):
        stub = self._stub(members_lease_pb(name="lease-old"))
        stub.DeleteLease = AsyncMock(side_effect=MockAioRpcError(grpc.StatusCode.UNAVAILABLE, "down"))
        with caplog.at_level("WARNING", logger="jumpstarter.client.grpc"), pytest.raises(ControllerTooOldError):
            await service(stub).CreateLease(duration=timedelta(minutes=45), members=MEMBERS, forwards=[forward()])
        assert "Could not delete lease lease-old" in caplog.text and "jmp delete leases lease-old" in caplog.text

    async def test_members_request_without_forwards_has_no_check(self):
        stub = self._stub(members_lease_pb())
        await service(stub).CreateLease(duration=timedelta(minutes=45), members=MEMBERS)
        stub.DeleteLease.assert_not_called()

    async def test_template_request_has_no_check(self):
        # The controller copies the forwards of the template. One without forwards
        # has no forwards in its templates, so the lease that it creates is complete.
        stub = self._stub(members_lease_pb(template="ivi"))
        lease = await service(stub).CreateLease(duration=None, template="ivi")
        assert lease.forwards == []
        assert not stub.CreateLease.call_args.args[0].lease.forwards
        stub.DeleteLease.assert_not_called()

    async def test_rejection_of_the_controller_keeps_its_reason(self):
        stub = Mock(
            CreateLease=AsyncMock(
                side_effect=MockAioRpcError(
                    grpc.StatusCode.INVALID_ARGUMENT, "forward endpoints must reference declared members"
                )
            )
        )
        with pytest.raises(LeaseError, match="forward endpoints must reference declared members"):
            await service(stub).CreateLease(duration=timedelta(minutes=5), members=MEMBERS, forwards=[forward()])
