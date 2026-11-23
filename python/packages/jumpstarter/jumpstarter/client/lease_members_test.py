from __future__ import annotations

from contextlib import ExitStack, asynccontextmanager, contextmanager
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import anyio
import grpc
import pytest
from anyio.from_thread import start_blocking_portal
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_network.client import NetworkClient
from jumpstarter_driver_power.client import PowerClient
from jumpstarter_driver_power.driver import MockPower
from jumpstarter_protocol import client_pb2, jumpstarter_pb2, kubernetes_pb2

from jumpstarter.client.exceptions import LeaseError, LeaseMemberLostError
from jumpstarter.client.grpc import Lease as LeaseModel
from jumpstarter.client.grpc import LeaseMember as MemberModel
from jumpstarter.client.grpc import LeaseTemplate
from jumpstarter.client.grpc_members_test import members_lease_pb, template_pb
from jumpstarter.client.lease import Lease, LeaseMembers, MembersLease, _MemberStub, member
from jumpstarter.client.lease_test import MockAioRpcError
from jumpstarter.common import ExporterStatus
from jumpstarter.common.utils import serve
from jumpstarter.exporter import Session
from jumpstarter.utils.env import ExporterMetadata


class Headunit(CompositeClient):
    power: PowerClient


class Phone(CompositeClient):
    power: PowerClient
    net: NetworkClient | None


class Projection(LeaseMembers, template="projection"):
    phone: Phone | None
    headunit: Headunit


def condition(type, status, reason="", message=""):
    return kubernetes_pb2.Condition(type=type, status=status, reason=reason, message=message)


READY = [condition("Ready", "True", "Ready")]


def lease_model(**kwargs) -> LeaseModel:
    return LeaseModel.from_protobuf(members_lease_pb(**kwargs))


def members(*specs) -> list[MemberModel]:
    return [MemberModel(name=name, selector="x=y", optional=optional) for name, optional in specs]


@contextmanager
def composite_client(**children):
    with serve(Composite(children=children)) as client:
        yield client


class TestLeaseMembersClass:
    def test_roles_come_from_composite_client_annotations(self):
        class Bench(LeaseMembers, template="bench"):
            head_unit: Headunit
            phone: Phone | None
            note: str
            power: PowerClient

        assert Bench.__lease_template__ == "bench"
        roles = Bench.__lease_roles__
        # `_` becomes `-`; an annotation that is not a CompositeClient subclass is not a role
        assert set(roles) == {"head-unit", "phone"}
        assert roles["head-unit"].attribute == "head_unit" and not roles["head-unit"].optional
        assert roles["phone"].optional and roles["phone"].client_class is Phone
        assert set(roles["phone"].drivers) == {"power", "net"}
        assert roles["phone"].drivers["net"].optional and not roles["phone"].drivers["power"].optional

    def test_member_gives_a_different_role_name(self):
        class Bench(LeaseMembers):
            recorder: Headunit = member(role="metadata")
            lookup: Headunit = member(role="by-role")

        assert set(Bench.__lease_roles__) == {"metadata", "by-role"}
        assert Bench.__lease_template__ is None

    @pytest.mark.parametrize("attribute", ["by_role", "metadata"])
    def test_reserved_names_cannot_be_roles(self, attribute):
        with pytest.raises(TypeError, match=f"'{attribute}' is an attribute of LeaseMembers"):
            type("Bench", (LeaseMembers,), {"__annotations__": {attribute: Headunit}})

    def test_member_needs_a_composite_client_annotation(self):
        with pytest.raises(TypeError, match="member\\(\\) needs a CompositeClient subclass"):

            class Bench(LeaseMembers):
                power: PowerClient = member(role="power")

    def test_two_attributes_cannot_be_one_role(self):
        with pytest.raises(TypeError, match="both the role 'head-unit'"):

            class Bench(LeaseMembers):
                head_unit: Headunit
                hu: Headunit = member(role="head-unit")

    def test_subclass_inherits_template_and_roles(self):
        class Wider(Projection):
            ecu: Headunit

        assert Wider.__lease_template__ == "projection"
        assert set(Wider.__lease_roles__) == {"phone", "headunit", "ecu"}

    def test_string_annotations_of_local_classes_resolve(self):
        class Local(CompositeClient):
            power: PowerClient

        class Bench(LeaseMembers):
            dut: Local | None

        assert Bench.__lease_roles__["dut"].client_class is Local and Bench.__lease_roles__["dut"].optional

    def test_unresolvable_annotation_is_a_type_error(self):
        with pytest.raises(TypeError, match="cannot resolve the annotation 'Missing'"):
            type("Bench", (LeaseMembers,), {"__annotations__": {"dut": "Missing"}, "__module__": __name__})


class TestCheckMembers:
    def test_agreement_returns_undeclared_optional_roles(self):
        template = members(("phone", True), ("headunit", False), ("ecu", True))
        assert Projection.check_members(template, "the lease template 'projection'") == ["ecu"]

    def test_role_that_the_template_does_not_contain(self):
        with pytest.raises(TypeError, match="declares the role 'phone', but the lease template 'p' has no such role"):
            Projection.check_members(members(("headunit", False)), "the lease template 'p'")

    def test_required_role_that_the_class_does_not_declare(self):
        template = members(("phone", True), ("headunit", False), ("ecu", False))
        with pytest.raises(TypeError, match="does not declare the required role 'ecu'"):
            Projection.check_members(template, "the lease template 'p'")

    def test_optional_role_needs_none_in_the_annotation(self):
        template = members(("phone", True), ("headunit", True))
        with pytest.raises(TypeError, match="role 'headunit' is optional .* must include `\\| None`"):
            Projection.check_members(template, "the lease template 'p'")

    def test_only_optional_roles_can_use_none(self):
        template = members(("phone", False), ("headunit", False))
        with pytest.raises(TypeError, match="role 'phone' is required .* cannot include `\\| None`"):
            Projection.check_members(template, "the lease template 'p'")

    def test_plain_lease_members_accepts_every_lease(self):
        assert LeaseMembers.check_members(members(("a", False), ("b", True)), "x") == []


class TestConnectCheck:
    def test_instance_has_typed_attributes_by_role_and_metadata(self):
        with composite_client(power=MockPower()) as client:
            metadata = {"headunit": ExporterMetadata(name="rack3-hu-2", lease="l"), "phone": None}
            projection = Projection({"headunit": client, "phone": None}, metadata)
            assert projection.headunit is client
            assert isinstance(projection.headunit, Headunit)
            assert projection.phone is None
            assert projection.headunit.power.on() is None
            assert dict(projection.by_role) == {"headunit": client, "phone": None}
            assert projection.metadata["headunit"] == ExporterMetadata(name="rack3-hu-2", lease="l")
            assert projection.metadata["phone"] is None
            with pytest.raises(TypeError):
                projection.by_role["x"] = client  # ty: ignore[invalid-assignment]  # read-only
            with pytest.raises(TypeError):
                projection.metadata["x"] = None  # ty: ignore[invalid-assignment]

    def test_missing_driver_names_role_and_driver(self):
        with composite_client(other=MockPower()) as client:  # noqa: SIM117
            with pytest.raises(TypeError, match="Projection: role 'headunit': the exporter has no driver 'power'"):
                Projection({"headunit": client, "phone": None}, {})

    def test_wrong_client_class_names_role_driver_and_reported_class(self):
        class Wrong(LeaseMembers):
            headunit: WrongHeadunit

        with (
            composite_client(power=MockPower()) as client,
            pytest.raises(
                TypeError,
                match="Wrong: role 'headunit': driver 'power' is jumpstarter_driver_power.client.PowerClient, "
                "expected jumpstarter_driver_network.client.NetworkClient",
            ),
        ):
            Wrong({"headunit": client}, {})

    def test_optional_driver_can_be_absent(self):
        with composite_client(power=MockPower()) as client:
            projection = Projection({"phone": client, "headunit": client}, {})
            assert isinstance(projection.phone, CompositeClient)

    def test_nested_member_class_is_checked(self):
        class Rig(CompositeClient):
            dut: Headunit

        class Bench(LeaseMembers):
            rig: Rig

        with composite_client(dut=Composite(children={"power": MockPower()})) as client:
            bench = Bench({"rig": client}, {})
            assert isinstance(bench.rig.dut, Headunit)
        with composite_client(dut=Composite(children={})) as client:  # noqa: SIM117
            with pytest.raises(TypeError, match="role 'rig': the exporter has no driver 'dut.power'"):
                Bench({"rig": client}, {})

    def test_undeclared_roles_are_only_in_by_role(self):
        with composite_client(power=MockPower()) as client:
            generic = LeaseMembers({"anything": client}, {"anything": None})
            assert generic.by_role["anything"] is client
            assert not hasattr(generic, "anything")


class WrongHeadunit(CompositeClient):
    power: NetworkClient


def make_lease(portal=None, **kwargs) -> Any:
    lease = MembersLease(
        channel=Mock(),
        namespace="default",
        portal=portal or Mock(),
        allow=[],
        unsafe=True,
        acquisition_timeout=30,
        **kwargs,
    )
    lease.svc = Mock()
    lease.svc.GetExporter = AsyncMock(return_value=Mock(labels={"device-type": "x"}))
    return lease


def lost_status(name="headunit", reason="Offline"):
    return client_pb2.LeaseMemberStatus(
        name=name,
        state=client_pb2.LEASE_MEMBER_STATE_LOST,
        exporter=f"namespaces/default/exporters/rack3-{name}",
        reason=reason,
        message="no heartbeat for 60s",
    )


@pytest.mark.anyio
class TestRequest:
    async def test_template_request_checks_the_class_first_and_excludes_undeclared_optional_roles(self):
        lease = make_lease(members_class=Projection, duration=timedelta(minutes=45))
        definition = template_pb("projection")
        del definition.members[:]
        for name, optional in (("phone", True), ("headunit", False), ("ecu", True)):
            definition.members.append(
                client_pb2.LeaseTemplateMember(
                    member=client_pb2.LeaseMember(name=name, selector="a=b", optional=optional)
                )
            )
        lease.svc.GetLeaseTemplate = AsyncMock(return_value=LeaseTemplate.from_protobuf(definition))
        lease.svc.CreateLease = AsyncMock(return_value=lease_model(name="projection-abc"))
        await lease._create()
        lease.svc.GetLeaseTemplate.assert_awaited_once_with(name="projection")
        kwargs = lease.svc.CreateLease.call_args.kwargs
        assert kwargs["template"] == "projection"
        assert kwargs["excluded_members"] == ["ecu"]  # as --without does
        assert kwargs["duration"] == timedelta(minutes=45)
        assert kwargs["members"] is None and kwargs["member_selectors"] is None
        assert lease.name == "projection-abc"

    async def test_class_that_disagrees_with_the_template_creates_no_lease(self):
        lease = make_lease(members_class=Projection)
        lease.svc.GetLeaseTemplate = AsyncMock(return_value=LeaseTemplate.from_protobuf(template_pb("projection")))
        lease.svc.CreateLease = AsyncMock()
        with pytest.raises(TypeError, match="does not declare the required role 'ecu'"):
            await lease.request_async()
        lease.svc.CreateLease.assert_not_called()

    async def test_template_default_duration_is_left_to_the_controller(self):
        lease = make_lease(template="ivi", member_selectors={"headunit": "soc=sa8775p"}, excluded_members=["phone"])
        lease.svc.GetLeaseTemplate = AsyncMock(return_value=LeaseTemplate.from_protobuf(template_pb()))
        lease.svc.CreateLease = AsyncMock(return_value=lease_model())
        await lease._create()
        kwargs = lease.svc.CreateLease.call_args.kwargs
        assert kwargs["duration"] is None
        assert kwargs["member_selectors"] == {"headunit": "soc=sa8775p"}
        assert kwargs["excluded_members"] == ["phone"]

    async def test_template_without_default_duration_gets_thirty_minutes(self):
        definition = template_pb()
        definition.ClearField("default_duration")
        lease = make_lease(template="ivi")
        lease.svc.GetLeaseTemplate = AsyncMock(return_value=LeaseTemplate.from_protobuf(definition))
        lease.svc.CreateLease = AsyncMock(return_value=lease_model())
        await lease._create()
        assert lease.svc.CreateLease.call_args.kwargs["duration"] == timedelta(minutes=30)

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"member_selectors": {"nope": "a=b"}}, "has no role 'nope'"),
            ({"excluded_members": ["headunit"]}, "has no optional role 'headunit' to exclude"),
        ],
    )
    async def test_request_can_only_narrow_roles_of_the_template(self, kwargs, message):
        lease = make_lease(template="ivi", **kwargs)
        lease.svc.GetLeaseTemplate = AsyncMock(return_value=LeaseTemplate.from_protobuf(template_pb()))
        lease.svc.CreateLease = AsyncMock()
        with pytest.raises(LeaseError, match=message):
            await lease._create()
        lease.svc.CreateLease.assert_not_called()

    async def test_ad_hoc_members_need_no_template(self):
        requested = [MemberModel(name="phone", selector="device-type=phone")]
        lease = make_lease(requested_members=requested, duration=timedelta(minutes=5))
        lease.svc.GetLeaseTemplate = AsyncMock()
        lease.svc.CreateLease = AsyncMock(return_value=lease_model())
        await lease._create()
        lease.svc.GetLeaseTemplate.assert_not_called()
        assert lease.svc.CreateLease.call_args.kwargs["members"] == requested
        assert lease.svc.CreateLease.call_args.kwargs["template"] is None

    async def test_class_without_template_can_only_attach(self):
        class Attach(LeaseMembers):
            headunit: Headunit

        lease = make_lease(members_class=Attach)
        with pytest.raises(LeaseError, match="Attach has no template: it can only attach to a lease that exists"):
            await lease.request_async()

    async def test_attach_to_a_scalar_lease_is_rejected(self):
        lease = make_lease(name="one")
        scalar = lease_model()
        scalar.members = []
        lease.get = AsyncMock(return_value=scalar)
        with pytest.raises(LeaseError, match="lease one is a lease of one exporter"):
            await lease.request_async()

    async def test_scalar_lease_object_rejects_a_lease_with_members(self):
        scalar = object.__new__(Lease)
        scalar.name = "lease-9m2qd"
        scalar.acquisition_timeout = 30
        scalar._get_with_retry = AsyncMock(return_value=lease_model(conditions=READY))
        with pytest.raises(LeaseError, match="has members \\(phone, headunit\\), not one exporter.*env_members"):
            await scalar._acquire()


@pytest.mark.anyio
class TestAcquire:
    async def _acquire(self, *polls, **kwargs):
        lease = make_lease(name="lease-9m2qd", **kwargs)
        lease.get = AsyncMock(side_effect=list(polls))
        with patch("jumpstarter.client.lease.sleep", AsyncMock()):
            await lease._acquire()
        return lease

    async def test_waits_for_ready_through_pending_and_hooks(self):
        lease = await self._acquire(
            lease_model(statuses=[], conditions=[condition("Pending", "True", "NotAvailable", "no exporter")]),
            lease_model(
                conditions=[
                    condition("Pending", "False", "Bound"),
                    condition("Ready", "False", "BeforeLeaseHook", "waiting for: headunit"),
                ]
            ),
            lease_model(conditions=[condition("Pending", "False"), condition("Ready", "True", "Ready")]),
        )
        assert lease.get.await_count == 3
        assert lease.roles == ["phone", "headunit"]
        assert lease.member_exporter("phone") == "rack3-phone-4"
        assert lease.owner == "ci-runner"
        assert lease.member_labels == {"phone": {"device-type": "x"}, "headunit": {"device-type": "x"}}
        assert lease.member_metadata()["headunit"] == ExporterMetadata(
            name="rack3-hu-2", labels={"device-type": "x"}, lease="lease-9m2qd"
        )

    async def test_lost_member_stops_the_wait_with_role_and_reason(self):
        with pytest.raises(
            LeaseError,
            match="lease lease-9m2qd: member 'headunit' lost its exporter rack3-headunit \\(Offline\\): no heartbeat",
        ):
            await self._acquire(
                lease_model(conditions=[condition("Ready", "False", "BeforeLeaseHook", "waiting for: headunit")]),
                lease_model(
                    statuses=[lost_status()],
                    conditions=[condition("Ready", "False", "MemberLost"), condition("Degraded", "True")],
                ),
            )

    async def test_released_by_exporter_is_reported_as_lost(self):
        with pytest.raises(LeaseError, match="member 'phone' lost its exporter rack3-phone \\(ExporterReleased\\)"):
            await self._acquire(lease_model(statuses=[lost_status("phone", "ExporterReleased")]))

    async def test_hook_failure_stops_the_wait_with_the_roles(self):
        with pytest.raises(
            LeaseError, match="beforeLease hook failed \\(BeforeLeaseHookFailed\\): hook failed for: headunit"
        ):
            await self._acquire(
                lease_model(conditions=[condition("Ready", "False", "BeforeLeaseHook", "waiting for: headunit")]),
                lease_model(
                    conditions=[condition("Ready", "False", "BeforeLeaseHookFailed", "hook failed for: headunit")]
                ),
            )

    @pytest.mark.parametrize(
        ("type", "message"),
        [("Unsatisfiable", "the lease cannot be satisfied: no bus"), ("Invalid", "the lease is invalid: no bus")],
    )
    async def test_unsatisfiable_and_invalid_end_the_wait(self, type, message):
        with pytest.raises(LeaseError, match=message):
            await self._acquire(lease_model(conditions=[condition(type, "True", "ConstraintUnsatisfiable", "no bus")]))

    async def test_ready_false_for_another_reason_ends_the_wait(self):
        with pytest.raises(LeaseError, match="lease lease-9m2qd: released"):
            await self._acquire(
                lease_model(
                    conditions=[condition("Pending", "False"), condition("Ready", "False", "Released", "released")]
                )
            )

    async def test_optional_role_without_exporter_is_none(self):
        lease = await self._acquire(
            lease_model(
                statuses=[
                    client_pb2.LeaseMemberStatus(
                        name="phone", state=client_pb2.LEASE_MEMBER_STATE_OMITTED, reason="Excluded"
                    ),
                    client_pb2.LeaseMemberStatus(
                        name="headunit",
                        state=client_pb2.LEASE_MEMBER_STATE_BOUND,
                        exporter="namespaces/default/exporters/rack3-hu-2",
                    ),
                ],
                conditions=READY,
            )
        )
        assert lease.member_exporter("phone") is None
        assert lease.member_metadata()["phone"] is None
        lease.svc.GetExporter.assert_awaited_once_with(name="rack3-hu-2")


@pytest.mark.anyio
class TestRelease:
    async def test_cancel_while_waiting_releases_the_lease_that_was_created(self):
        lease = make_lease(template="ivi")
        lease.svc.GetLeaseTemplate = AsyncMock(return_value=LeaseTemplate.from_protobuf(template_pb()))
        lease.svc.CreateLease = AsyncMock(return_value=lease_model(name="ivi-4fj2k"))
        lease.svc.DeleteLease = AsyncMock()
        lease.get = AsyncMock(return_value=lease_model(statuses=[], conditions=[condition("Pending", "True")]))
        with anyio.move_on_after(0.2):
            async with lease:
                raise AssertionError("the lease never becomes Ready")
        lease.svc.DeleteLease.assert_awaited_once()
        assert lease.svc.DeleteLease.call_args.kwargs["name"] == "ivi-4fj2k"

    async def test_exit_releases_a_created_lease_and_keeps_an_attached_one(self):
        for name, release in ((None, True), ("lease-9m2qd", False)):
            lease = make_lease(template="ivi", name=name, release=release)
            lease.svc.GetLeaseTemplate = AsyncMock(return_value=LeaseTemplate.from_protobuf(template_pb()))
            lease.svc.CreateLease = AsyncMock(return_value=lease_model(name="ivi-4fj2k"))
            lease.svc.DeleteLease = AsyncMock()
            lease.get = AsyncMock(return_value=lease_model(conditions=READY))
            async with lease:
                pass
            assert lease.svc.DeleteLease.await_count == (1 if release else 0)


@pytest.mark.anyio
class TestDialAndServe:
    async def test_dial_names_the_member(self):
        lease = make_lease(name="lease-9m2qd")
        lease.controller = Mock(Dial=AsyncMock(return_value=Mock()))
        await lease._dial_with_retry("phone", "rack3-phone-4")
        request = lease.controller.Dial.call_args.args[0]
        assert request == jumpstarter_pb2.DialRequest(lease_name="lease-9m2qd", member_name="phone")

    async def test_scalar_dial_sends_no_member_name(self):
        lease = make_lease(name="one")
        lease.controller = Mock(Dial=AsyncMock(return_value=Mock()))
        await Lease._dial_with_retry(lease)
        assert not lease.controller.Dial.call_args.args[0].HasField("member_name")

    async def test_dial_of_a_lost_member_does_not_wait_for_it(self):
        lease = make_lease(name="lease-9m2qd", dial_timeout=30.0)
        lease.controller = Mock(
            Dial=AsyncMock(
                side_effect=MockAioRpcError(grpc.StatusCode.FAILED_PRECONDITION, "member headunit is not ready: Lost")
            )
        )
        lease.get = AsyncMock(return_value=lease_model(statuses=[lost_status()]))
        with pytest.raises(LeaseMemberLostError, match="member 'headunit' lost its exporter"):
            await lease._dial_with_retry("headunit", "rack3-hu-2")
        assert lease.controller.Dial.await_count == 1

    async def test_one_socket_for_each_bound_role_and_none_for_the_rest(self, tmp_path):
        lease = make_lease(name="lease-9m2qd")
        lease._record(
            lease_model(
                statuses=[
                    client_pb2.LeaseMemberStatus(name="phone", state=client_pb2.LEASE_MEMBER_STATE_OMITTED),
                    client_pb2.LeaseMemberStatus(
                        name="headunit",
                        state=client_pb2.LEASE_MEMBER_STATE_BOUND,
                        exporter="namespaces/default/exporters/rack3-hu-2",
                    ),
                ]
            )
        )
        served = []

        @asynccontextmanager
        async def serve_tunnel(member_name=None, exporter_name=None, path=None):
            served.append((member_name, exporter_name))
            yield path

        lease._serve_tunnel = serve_tunnel
        async with lease.serve_unix_async() as paths:
            assert paths["phone"] is None
            assert paths["headunit"].endswith("/headunit.sock")
        assert served == [("headunit", "rack3-hu-2")]

    async def test_initial_dial_of_each_bound_role(self):
        lease = make_lease(name="lease-9m2qd")
        lease._record(lease_model())
        lease.controller = Mock(Dial=AsyncMock(return_value=Mock()))
        async with lease.serve_unix_async() as paths:
            assert set(paths) == {"phone", "headunit"} and all(paths.values())
        dialed = [call.args[0].member_name for call in lease.controller.Dial.call_args_list]
        assert dialed == ["phone", "headunit"]


class TestConnect:
    """connect() against real exporter sessions; only the controller tunnel is replaced."""

    @contextmanager
    def _lease(self, members_class, *, phone_bound=True, headunit_children=None):
        with ExitStack() as stack, start_blocking_portal() as portal:
            sockets = {}
            for role, children in (("phone", {"power": MockPower()}), ("headunit", headunit_children)):
                session = stack.enter_context(
                    Session(root_device=Composite(children=dict(children or {"power": MockPower()})))
                )
                sockets[role] = stack.enter_context(session.serve_unix())
                session.update_status(ExporterStatus.LEASE_READY)
            lease = make_lease(portal=portal, name="lease-9m2qd", members_class=members_class)
            statuses = None
            if not phone_bound:
                statuses = [
                    client_pb2.LeaseMemberStatus(name="phone", state=client_pb2.LEASE_MEMBER_STATE_OMITTED),
                    client_pb2.LeaseMemberStatus(
                        name="headunit",
                        state=client_pb2.LEASE_MEMBER_STATE_BOUND,
                        exporter="namespaces/default/exporters/rack3-hu-2",
                    ),
                ]
            lease._record(lease_model(statuses=statuses))
            lease.member_labels = {"headunit": {"device-type": "headunit"}}

            @asynccontextmanager
            async def serve_unix_async():
                yield {role: str(sockets[role]) if lease.member_exporter(role) else None for role in lease.roles}

            lease.serve_unix_async = serve_unix_async
            yield lease

    def test_members_class_gives_an_instance_of_that_class(self):
        with self._lease(Projection) as lease, lease.connect() as projection:
            assert type(projection) is Projection
            assert isinstance(projection.headunit, Headunit)
            projection.headunit.power.on()
            projection.phone.power.off()
            assert projection.metadata["headunit"] == ExporterMetadata(
                name="rack3-hu-2", labels={"device-type": "headunit"}, lease="lease-9m2qd"
            )
            assert set(projection.by_role) == {"phone", "headunit"}

    def test_template_name_gives_plain_lease_members(self):
        with self._lease(LeaseMembers) as lease, lease.connect() as generic:
            assert type(generic) is LeaseMembers
            generic.by_role["headunit"].power.on()

    def test_optional_role_without_exporter_is_none(self):
        with self._lease(Projection, phone_bound=False) as lease, lease.connect() as projection:
            assert projection.phone is None
            assert projection.metadata["phone"] is None
            projection.headunit.power.on()

    def test_exporter_that_does_not_match_its_member_class(self):
        with self._lease(Projection, headunit_children={"relay": MockPower()}) as lease:  # noqa: SIM117
            with pytest.raises(TypeError, match="role 'headunit': the exporter has no driver 'power'"):
                with lease.connect():
                    pass

    def test_attach_compares_the_class_with_the_members_of_the_lease(self):
        class Other(LeaseMembers):
            ecu: Headunit

        with self._lease(Other) as lease:  # noqa: SIM117
            with pytest.raises(TypeError, match="Other declares the role 'ecu', but the lease 'lease-9m2qd' has no"):
                with lease.connect():
                    pass

    def test_call_to_a_role_that_is_known_lost(self):
        with self._lease(Projection) as lease, lease.connect() as projection:
            projection.headunit.power.on()
            lease.lost["headunit"] = "member 'headunit' lost its exporter rack3-hu-2 (Offline)"
            with pytest.raises(LeaseMemberLostError, match="lease lease-9m2qd: member 'headunit' lost its exporter"):
                projection.headunit.power.on()
            # the other members stay usable
            projection.phone.power.on()


@pytest.mark.anyio
class TestMemberStub:
    async def test_failed_call_reads_the_lease_and_reports_the_lost_role(self):
        lease = make_lease(name="lease-9m2qd")
        lease.get = AsyncMock(return_value=lease_model(statuses=[lost_status()]))
        inner = Mock(DriverCall=AsyncMock(side_effect=MockAioRpcError(grpc.StatusCode.UNAVAILABLE, "socket closed")))
        stub = _MemberStub(inner, "headunit", lease)
        with pytest.raises(LeaseMemberLostError, match="member 'headunit' lost its exporter rack3-headunit"):
            await stub.DriverCall(Mock())
        # known now: the next call does not reach the exporter
        with pytest.raises(LeaseMemberLostError):
            stub.GetStatus  # noqa: B018
        assert inner.DriverCall.await_count == 1

    async def test_failed_call_of_a_bound_role_keeps_its_error(self):
        lease = make_lease(name="lease-9m2qd")
        lease.get = AsyncMock(return_value=lease_model())
        inner = Mock(DriverCall=AsyncMock(side_effect=MockAioRpcError(grpc.StatusCode.UNAVAILABLE, "blip")))
        with pytest.raises(grpc.aio.AioRpcError):
            await _MemberStub(inner, "headunit", lease).DriverCall(Mock())

    async def test_driver_error_does_not_read_the_lease(self):
        lease = make_lease(name="lease-9m2qd")
        lease.get = AsyncMock()
        inner = Mock(DriverCall=AsyncMock(side_effect=MockAioRpcError(grpc.StatusCode.UNKNOWN, "driver raised")))
        with pytest.raises(grpc.aio.AioRpcError):
            await _MemberStub(inner, "headunit", lease).DriverCall(Mock())
        lease.get.assert_not_called()
