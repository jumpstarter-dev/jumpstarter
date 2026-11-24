"""The `Forward` type, and a lease with forwards in the Python client (JEP-0016)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from anyio.from_thread import start_blocking_portal
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_power.client import PowerClient

from jumpstarter.client import Forward as ExportedForward
from jumpstarter.client import forward as exported_forward
from jumpstarter.client._members_testing import (
    exporter_pb,
    forward_pb,
    forward_status_pb,
    template_pb,
)
from jumpstarter.client.exceptions import ForwardError, LeaseError
from jumpstarter.client.grpc import Exporter, ForwardEndpoint, LeaseForwardStatus, LeaseTemplate
from jumpstarter.client.grpc import LeaseForward as ForwardModel
from jumpstarter.client.grpc import LeaseMember as MemberModel
from jumpstarter.client.lease import Forward, LeaseMembers, _ForwardStatusSource, forward, member
from jumpstarter.client.lease_members_test import (
    DEGRADED,
    READY,
    TestConnect,
    condition,
    lease_model,
    lost_status,
    make_lease,
)


class Peer(CompositeClient):
    power: PowerClient


class Headunit(CompositeClient):
    power: PowerClient


class HeadunitPeer(LeaseMembers, template="headunit-peer"):
    phone: Peer | None
    headunit: Headunit
    bt: Forward


# The conditions as the controller writes them. While a forward is Pending, Ready has the
# reason BeforeLeaseHook (a client that does not know forwards waits for it), and
# ForwardsReady has the reason ForwardsPending.
FORWARDS_PENDING = [
    condition("Pending", "False", "Bound"),
    condition("Ready", "False", "BeforeLeaseHook", "Waiting for forwards: `bt`"),
    condition("ForwardsReady", "False", "ForwardsPending", "Waiting for forwards: `bt`"),
]
# The hooks of the members are complete: only the forward keeps the lease from Ready.
FORWARD_FAILED = [
    condition("Ready", "False", "Failed", "Failed forwards: `bt`"),
    condition("ForwardsReady", "False", "Failed", "forward bt failed"),
    condition("Degraded", "True", "Failed"),
]
# A forward failed the validation at the bind, and a beforeLease hook still runs.
FORWARD_FAILED_IN_HOOK = [
    condition("Ready", "False", "BeforeLeaseHook", "Waiting for the beforeLease hook of roles: `headunit`"),
    *FORWARD_FAILED[1:],
]
FAILURE = "port 'rootcanal' is not on exporter rack3-hu-2"


def forwards_lease(state="Listening", message=None, conditions=READY, **kwargs):
    return lease_model(
        forwards=[forward_pb()],
        forward_statuses=[forward_status_pb("bt", state, message)] if state else [],
        conditions=conditions,
        **kwargs,
    )


def status(state, message=None) -> LeaseForwardStatus:
    return LeaseForwardStatus(name="bt", state=state, message=message)


class Reads:
    """The reads of the lease that a forward makes: each gives the next state, and the last one stays."""

    def __init__(self, *states):
        self.states = list(states)
        self.count = 0

    async def __call__(self):
        self.count += 1
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if isinstance(state, Exception):
            raise state
        return [state] if isinstance(state, LeaseForwardStatus) else [status(state)]


@pytest.fixture
def portal():
    with start_blocking_portal() as portal:
        yield portal


def sourced(portal, reads, initial="Pending") -> Forward:
    source = _ForwardStatusSource("lease-9m2qd", reads, portal, [status(initial)])
    return Forward("bt", [ForwardEndpoint(member_name="headunit", port_name="rootcanal")], source=source)


class TestForwardDeclaration:
    def test_forward_annotation_is_a_forward_of_the_template(self):
        class Bench(LeaseMembers, template="bench"):
            headunit: Headunit
            bt: Forward
            bt_link: Forward
            link: Forward = forward(name="rootcanal-link")
            note: str

        assert set(Bench.__lease_forwards__) == {"bt", "bt-link", "rootcanal-link"}  # `_` becomes `-`
        assert Bench.__lease_forwards__["bt-link"].attribute == "bt_link"
        assert Bench.__lease_forwards__["rootcanal-link"].attribute == "link"
        # a forward is not a role, and a role is not a forward
        assert set(Bench.__lease_roles__) == {"headunit"}

    def test_public_names(self):
        assert ExportedForward is Forward and exported_forward is forward

    def test_subclass_inherits_the_forwards(self):
        class Wider(HeadunitPeer):
            can: Forward

        assert set(Wider.__lease_forwards__) == {"bt", "can"}
        assert Wider.__lease_template__ == "headunit-peer"

    def test_plain_lease_members_declares_no_forward(self):
        assert LeaseMembers.__lease_forwards__ == {}

    def test_forward_cannot_be_optional(self):
        with pytest.raises(TypeError, match="Bench.bt: a forward attribute is always a Forward object.*`\\| None`"):

            class Bench(LeaseMembers):
                bt: Forward | None

    def test_forward_marker_needs_the_forward_annotation(self):
        with pytest.raises(TypeError, match="Bench.bt: forward\\(\\) needs `Forward` as the annotation"):

            class Bench(LeaseMembers):
                bt: Headunit = forward(name="bt")

    def test_member_marker_on_a_forward_is_rejected(self):
        with pytest.raises(TypeError, match="member\\(\\) needs a CompositeClient subclass"):

            class Bench(LeaseMembers):
                bt: Forward = member(role="bt")

    def test_forward_and_role_cannot_have_the_same_name(self):
        with pytest.raises(TypeError, match="'headunit' is a role: a forward and a role of one lease cannot"):

            class Bench(LeaseMembers):
                headunit: Headunit
                link: Forward = forward(name="headunit")

    def test_two_attributes_cannot_be_one_forward(self):
        with pytest.raises(TypeError, match="'bt' and 'link' are both the forward 'bt'"):

            class Bench(LeaseMembers):
                bt: Forward
                link: Forward = forward(name="bt")

    @pytest.mark.parametrize("annotation", ["Forward", "Headunit"])
    def test_forwards_is_an_attribute_of_lease_members(self, annotation):
        with pytest.raises(TypeError, match="'forwards' is an attribute of LeaseMembers"):
            LeaseMembers.__class__("Bench", (LeaseMembers,), {"__annotations__": {"forwards": annotation}})


class TestCheckForwards:
    def test_declared_forward_that_the_template_contains(self):
        HeadunitPeer.check_forwards([ForwardModel.from_protobuf(forward_pb())], "the lease template 'headunit-peer'")
        HeadunitPeer.check_forwards(["bt", "can"], "the lease")  # names are enough

    def test_declared_forward_that_the_template_does_not_contain(self):
        with pytest.raises(
            TypeError,
            match="HeadunitPeer.bt: HeadunitPeer declares the forward 'bt', but the lease template 'x' has no such "
            "forward \\(forwards: can\\)",
        ):
            HeadunitPeer.check_forwards([ForwardModel.from_protobuf(forward_pb("can"))], "the lease template 'x'")
        with pytest.raises(TypeError, match="\\(forwards: none\\)"):
            HeadunitPeer.check_forwards([], "the lease template 'x'")

    def test_class_can_leave_out_a_forward_of_the_template(self):
        class NoForwards(LeaseMembers, template="headunit-peer"):
            headunit: Headunit

        NoForwards.check_forwards(["bt"], "the lease template 'headunit-peer'")
        LeaseMembers.check_forwards(["bt"], "the lease")


class TestMembersObject:
    def test_declared_forward_is_an_attribute_and_all_are_in_forwards(self):
        bt, can = Forward("bt", state="Listening"), Forward("can", state="Disabled")
        members = HeadunitPeer({"phone": None, "headunit": None}, {}, {"bt": bt, "can": can})
        assert members.bt is bt and isinstance(members.bt, Forward)
        assert dict(members.forwards) == {"bt": bt, "can": can}
        read_only: Any = members.forwards
        with pytest.raises(TypeError):
            read_only["x"] = bt

    def test_generic_members_have_the_forwards_by_name(self):
        bt = Forward("bt", state="Connected")
        members = LeaseMembers({"headunit": None}, {}, {"bt": bt})
        assert members.forwards["bt"].state == "Connected"
        assert not hasattr(members, "bt")

    def test_members_without_forwards(self):
        assert dict(LeaseMembers({}, {}).forwards) == {}

    def test_declared_forward_that_the_lease_does_not_contain(self):
        with pytest.raises(TypeError, match="HeadunitPeer declares the forward 'bt', but the lease has no such"):
            HeadunitPeer({"phone": None, "headunit": None}, {})

    def test_disabled_forward_is_a_forward_object_with_that_state(self):
        disabled = Forward("bt", state="Disabled", message="member 'phone' is omitted")
        members = HeadunitPeer({"phone": None, "headunit": None}, {}, {"bt": disabled})
        assert isinstance(members.bt, Forward) and members.bt.state == "Disabled"
        assert members.bt.message == "member 'phone' is omitted"


class TestForwardObject:
    def test_name_state_message_and_endpoints(self):
        endpoints = [
            ForwardEndpoint(member_name="headunit", port_name="rootcanal"),
            ForwardEndpoint(member_name="peer", port_name="controller"),
        ]
        bt = Forward("bt", endpoints, state="Failed", message=FAILURE)
        assert (bt.name, bt.state, bt.message) == ("bt", "Failed", FAILURE)
        assert bt.endpoints == tuple(endpoints)
        assert repr(bt) == "<Forward 'bt' (headunit.rootcanal, peer.controller): Failed>"
        assert Forward("bt").state == "Pending" and Forward("bt").message is None

    @pytest.mark.parametrize("state", ["Listening", "Connected"])
    def test_wait_listening_returns_for_a_forward_that_operates(self, state):
        bt = Forward("bt", state=state)
        assert bt.wait_listening(timeout=1) is bt

    @pytest.mark.parametrize(
        "state,text", [("Failed", "forward 'bt' failed: "), ("Disabled", "forward 'bt' is disabled: ")]
    )
    def test_wait_listening_raises_with_the_forward_and_the_cause(self, state, text):
        with pytest.raises(ForwardError, match=text + FAILURE) as excinfo:
            Forward("bt", state=state, message=FAILURE).wait_listening(timeout=1)
        assert (excinfo.value.forward, excinfo.value.state, excinfo.value.cause) == ("bt", state, FAILURE)
        assert isinstance(excinfo.value, LeaseError)

    def test_failure_without_a_message(self):
        with pytest.raises(ForwardError, match="^forward 'bt' failed$") as excinfo:
            Forward("bt", state="Failed").wait_listening()
        assert excinfo.value.cause is None

    def test_pending_forward_without_a_lease_to_read_times_out_at_once(self):
        with pytest.raises(TimeoutError, match="forward 'bt' is still Pending after 30s"):
            Forward("bt").wait_listening(timeout=30)

    def test_state_reads_the_lease_again_when_the_last_read_is_old(self, portal):
        reads = Reads("Listening", "Connected")
        bt = sourced(portal, reads)
        assert bt.state == "Pending" and reads.count == 0  # the read of the acquisition is still current
        with patch("jumpstarter.client.lease._FORWARD_STATE_MAX_AGE", 0):
            assert bt.state == "Listening"
            assert bt.state == "Connected"
        assert reads.count == 2
        assert bt.state == "Connected" and reads.count == 2  # current again

    def test_message_comes_from_the_same_read(self, portal):
        bt = sourced(portal, Reads(status("Failed", FAILURE)))
        with patch("jumpstarter.client.lease._FORWARD_STATE_MAX_AGE", 0):
            assert bt.message == FAILURE
        assert bt.state == "Failed"

    def test_state_keeps_the_last_read_when_the_lease_cannot_be_read(self, portal):
        bt = sourced(portal, Reads(RuntimeError("controller is down")), initial="Listening")
        with patch("jumpstarter.client.lease._FORWARD_STATE_MAX_AGE", 0):
            assert bt.state == "Listening"

    def test_forward_that_the_lease_does_not_report_is_pending(self, portal):
        reads = Reads(LeaseForwardStatus(name="other", state="Listening"))
        bt = sourced(portal, reads, initial="Listening")
        assert bt.refresh().state == "Pending"

    def test_refresh_reads_now_and_raises_when_it_cannot(self, portal):
        bt = sourced(portal, Reads("Connected"))
        assert bt.refresh() is bt and bt.state == "Connected"
        with pytest.raises(RuntimeError, match="controller is down"):
            sourced(portal, Reads(RuntimeError("controller is down"))).refresh()

    def test_source_without_a_way_to_read_says_why(self):
        source = _ForwardStatusSource("lease-9m2qd", None, None, [status("Pending")], unreadable="no client config")
        with pytest.raises(LeaseError, match="cannot read the forwards of lease lease-9m2qd: no client config"):
            Forward("bt", source=source).refresh()

    def test_wait_listening_polls_until_the_forward_listens(self, portal):
        reads = Reads("Pending", "Pending", "Listening")
        bt = sourced(portal, reads)
        with patch("jumpstarter.client.lease._FORWARD_POLL_SECONDS", 0):
            assert bt.wait_listening(timeout=5) is bt
        assert reads.count == 3 and bt.state == "Listening"

    def test_wait_listening_after_an_interruption(self, portal):
        # The forward was Connected, a stream was cut, and the controller sets it up again.
        reads = Reads("Pending", "Listening")
        bt = sourced(portal, reads, initial="Connected")
        with patch("jumpstarter.client.lease._FORWARD_POLL_SECONDS", 0):
            bt.wait_listening(timeout=5)
        assert reads.count == 2

    def test_wait_listening_stops_when_the_forward_fails(self, portal):
        bt = sourced(portal, Reads("Pending", status("Failed", FAILURE)))
        with (
            patch("jumpstarter.client.lease._FORWARD_POLL_SECONDS", 0),
            pytest.raises(ForwardError, match=f"forward 'bt' of lease lease-9m2qd failed: {FAILURE}") as excinfo,
        ):
            bt.wait_listening(timeout=5)
        assert excinfo.value.forward == "bt" and excinfo.value.cause == FAILURE

    def test_wait_listening_times_out_while_pending(self, portal):
        bt = sourced(portal, Reads("Pending"))
        with (
            patch("jumpstarter.client.lease._FORWARD_POLL_SECONDS", 0.01),
            pytest.raises(TimeoutError, match="forward 'bt' of lease lease-9m2qd is still Pending after 0.05s"),
        ):
            bt.wait_listening(timeout=0.05)

    def test_wait_listening_goes_on_through_a_failed_read(self, portal):
        reads = Reads(RuntimeError("controller is down"), "Listening")
        with patch("jumpstarter.client.lease._FORWARD_POLL_SECONDS", 0):
            sourced(portal, reads).wait_listening(timeout=5)
        assert reads.count == 2

    def test_timeout_names_the_read_that_failed(self, portal):
        bt = sourced(portal, Reads(RuntimeError("controller is down")))
        with (
            patch("jumpstarter.client.lease._FORWARD_POLL_SECONDS", 0.01),
            pytest.raises(TimeoutError, match="the last read of the lease failed: controller is down"),
        ):
            bt.wait_listening(timeout=0.05)

    @pytest.mark.anyio
    async def test_async_methods(self):
        reads = Reads("Pending", "Listening")
        source = _ForwardStatusSource("lease-9m2qd", reads, None, [status("Pending")])
        bt = Forward("bt", source=source)
        with patch("jumpstarter.client.lease._FORWARD_STATE_MAX_AGE", 0):
            # in async code the state is the last read: a blocking read is not possible
            assert bt.state == "Pending" and reads.count == 0
        assert (await bt.refresh_async()).state == "Pending"
        with patch("jumpstarter.client.lease._FORWARD_POLL_SECONDS", 0):
            assert await bt.wait_listening_async(timeout=5) is bt
        failed = Forward("bt", source=_ForwardStatusSource("l", Reads(status("Failed", FAILURE)), None))
        with pytest.raises(ForwardError, match=FAILURE):
            await failed.wait_listening_async(timeout=5)


@pytest.mark.anyio
class TestRequestWithForwards:
    async def test_ad_hoc_members_send_their_forwards(self):
        requested = [ForwardModel.from_protobuf(forward_pb())]
        lease = make_lease(
            duration=timedelta(minutes=45),
            requested_members=[MemberModel(name="phone", selector="a=b"), MemberModel(name="headunit", selector="c=d")],
            requested_forwards=requested,
        )
        lease.svc.CreateLease = AsyncMock(return_value=forwards_lease())
        await lease._create()
        kwargs = lease.svc.CreateLease.call_args.kwargs
        assert kwargs["forwards"] == requested and kwargs["template"] is None

    async def test_template_request_sends_no_forwards(self):
        lease = make_lease(members_class=HeadunitPeer)
        definition = template_pb("headunit-peer", forwards=[forward_pb()])
        del definition.members[2]  # headunit and the optional phone are left
        lease.svc.GetLeaseTemplate = AsyncMock(return_value=LeaseTemplate.from_protobuf(definition))
        lease.svc.CreateLease = AsyncMock(return_value=forwards_lease())
        await lease._create()
        assert lease.svc.CreateLease.call_args.kwargs["forwards"] is None

    async def test_forwards_with_a_template_are_rejected_before_any_request(self):
        lease = make_lease(template="ivi", requested_forwards=[ForwardModel.from_protobuf(forward_pb())])
        lease.svc.GetLeaseTemplate = AsyncMock()
        lease.svc.CreateLease = AsyncMock()
        with pytest.raises(LeaseError, match="forwards cannot be used with the lease template 'ivi'"):
            await lease._create()
        lease.svc.GetLeaseTemplate.assert_not_called()
        lease.svc.CreateLease.assert_not_called()

    async def test_class_forward_that_the_template_does_not_contain_creates_no_lease(self):
        lease = make_lease(members_class=HeadunitPeer)
        definition = template_pb("headunit-peer", forwards=[forward_pb("can")])
        del definition.members[2]
        lease.svc.GetLeaseTemplate = AsyncMock(return_value=LeaseTemplate.from_protobuf(definition))
        lease.svc.CreateLease = AsyncMock()
        with pytest.raises(TypeError, match="declares the forward 'bt', but the lease template 'headunit-peer' has"):
            await lease.request_async()
        lease.svc.CreateLease.assert_not_called()


@pytest.mark.anyio
class TestAcquireWithForwards:
    async def _wait(self, *polls, name="lease-9m2qd", attach=False, **kwargs):
        lease = make_lease(name=name, release=False, **kwargs)
        lease.get = AsyncMock(side_effect=list(polls))
        with patch("jumpstarter.client.lease.sleep", AsyncMock()):
            if attach:
                await lease.request_async()
            else:
                await lease._acquire()
        return lease

    async def test_pending_forward_keeps_the_wait(self):
        lease = await self._wait(
            forwards_lease("Pending", conditions=FORWARDS_PENDING),
            # a forward that the controller did not report yet is pending too
            forwards_lease(None, conditions=FORWARDS_PENDING),
            forwards_lease("Listening"),
        )
        assert lease.get.await_count == 3
        assert [f.name for f in lease.lease_forwards] == ["bt"]
        assert lease.forward_statuses["bt"].state == "Listening" and lease.failed_forwards == {}

    async def test_ready_with_a_connected_forward(self):
        lease = await self._wait(forwards_lease("Connected"))
        assert lease.forward_statuses["bt"].state == "Connected"

    async def test_disabled_forward_does_not_prevent_ready(self):
        lease = await self._wait(forwards_lease("Disabled", "member 'phone' is omitted"))
        forwards = lease.forward_objects()
        assert forwards["bt"].state == "Disabled" and forwards["bt"].message == "member 'phone' is omitted"
        assert lease.failed_forwards == {}

    async def test_failed_forward_stops_the_request_with_the_forward_and_the_cause(self):
        with pytest.raises(ForwardError, match=f"lease lease-9m2qd: forward 'bt' failed: {FAILURE}") as excinfo:
            await self._wait(
                forwards_lease("Pending", conditions=FORWARDS_PENDING),
                forwards_lease("Failed", FAILURE, conditions=FORWARD_FAILED),
                forwards_lease("Listening"),  # not read: the wait stopped
            )
        assert (excinfo.value.forward, excinfo.value.state, excinfo.value.cause) == ("bt", "Failed", FAILURE)

    async def test_failed_condition_without_a_failed_status_stops_the_request(self):
        # The condition can be written before the status of the forward.
        with pytest.raises(ForwardError, match="lease lease-9m2qd: a forward failed: forward bt failed"):
            await self._wait(forwards_lease("Pending", conditions=FORWARD_FAILED))

    async def test_request_releases_the_lease_that_has_a_failed_forward(self):
        lease: Any = make_lease(template="ivi", duration=timedelta(minutes=5))
        lease.svc.GetLeaseTemplate = AsyncMock(return_value=LeaseTemplate.from_protobuf(template_pb()))
        lease.svc.CreateLease = AsyncMock(return_value=forwards_lease("Pending"))
        lease.svc.DeleteLease = AsyncMock()
        lease.get = AsyncMock(return_value=forwards_lease("Failed", FAILURE, conditions=FORWARD_FAILED))
        with patch("jumpstarter.client.lease.sleep", AsyncMock()), pytest.raises(ForwardError):
            async with lease:
                raise AssertionError("the lease is not usable")
        lease.svc.DeleteLease.assert_awaited_once()

    async def test_hook_failure_is_reported_before_a_failed_forward(self):
        conditions = [condition("Ready", "False", "BeforeLeaseHookFailed", "headunit: exit 1"), *FORWARD_FAILED[1:]]
        with pytest.raises(LeaseError, match="beforeLease hook failed") as excinfo:
            await self._wait(forwards_lease("Failed", FAILURE, conditions=conditions))
        assert not isinstance(excinfo.value, ForwardError)

    async def test_attach_takes_a_lease_with_a_failed_forward_as_it_is(self, caplog):
        failed = forwards_lease("Failed", FAILURE, conditions=FORWARD_FAILED)
        with caplog.at_level("WARNING", logger="jumpstarter.client.lease"):
            # one read to verify the lease, one poll: no wait
            lease = await self._wait(failed, failed, attach=True)
        assert lease.attached and lease.get.await_count == 2
        assert lease.failed_forwards == {"bt": f"forward 'bt' failed: {FAILURE}"}
        assert f"Lease lease-9m2qd is degraded: forward 'bt' failed: {FAILURE}. The members stay usable." in caplog.text
        assert lease.member_exporter("headunit") == "rack3-hu-2"  # the members stay bound
        assert lease.forward_objects()["bt"].state == "Failed"

    async def test_attach_waits_for_a_pending_forward(self):
        pending = forwards_lease("Pending", conditions=FORWARDS_PENDING)
        lease = await self._wait(pending, pending, forwards_lease("Listening"), attach=True)
        assert lease.get.await_count == 3 and lease.failed_forwards == {}

    async def test_attach_with_a_lost_member_and_a_failed_forward_names_the_two(self, caplog):
        degraded = forwards_lease(
            "Failed", "member 'headunit' is lost", conditions=DEGRADED, statuses=[lost_status()]
        )
        with caplog.at_level("WARNING", logger="jumpstarter.client.lease"):
            lease = await self._wait(degraded, degraded, attach=True)
        assert "member 'headunit' lost its exporter" in caplog.text
        assert "forward 'bt' failed: member 'headunit' is lost. The other members stay usable." in caplog.text
        assert set(lease.lost) == {"headunit"} and set(lease.failed_forwards) == {"bt"}

    async def test_request_can_take_a_failed_forward_as_it_is(self, caplog):
        # What `jmp shell` asks for: the shell opens, with the cause as a warning.
        with caplog.at_level("WARNING", logger="jumpstarter.client.lease"):
            lease = await self._wait(
                forwards_lease("Pending", conditions=FORWARDS_PENDING),
                forwards_lease("Failed", FAILURE, conditions=FORWARD_FAILED),
                tolerate_failed_forwards=True,
            )
        assert not lease.attached and lease.get.await_count == 2
        assert lease.failed_forwards == {"bt": f"forward 'bt' failed: {FAILURE}"}
        assert f"forward 'bt' failed: {FAILURE}" in caplog.text

    async def test_request_that_takes_a_failed_forward_waits_for_the_hooks(self, caplog):
        # `jmp shell`: the forward failed at the bind, and the beforeLease hooks still run.
        in_hook = forwards_lease("Failed", FAILURE, conditions=FORWARD_FAILED_IN_HOOK)
        with caplog.at_level("WARNING", logger="jumpstarter.client.lease"):
            lease = await self._wait(
                forwards_lease("Failed", FAILURE, conditions=[condition("Pending", "True", "Pending")]),
                in_hook,
                in_hook,
                forwards_lease("Failed", FAILURE, conditions=FORWARD_FAILED),
                tolerate_failed_forwards=True,
            )
        # The lease is used only after the hooks.
        assert lease.get.await_count == 4
        assert lease.failed_forwards == {"bt": f"forward 'bt' failed: {FAILURE}"}
        assert caplog.text.count("is degraded") == 1

    async def test_request_that_takes_a_failed_forward_stops_for_a_failed_hook(self):
        in_hook = forwards_lease("Failed", FAILURE, conditions=FORWARD_FAILED_IN_HOOK)
        hook_failed = [condition("Ready", "False", "BeforeLeaseHookFailed", "headunit: exit 1"), *FORWARD_FAILED[1:]]
        with pytest.raises(LeaseError, match="beforeLease hook failed") as excinfo:
            await self._wait(
                in_hook,
                forwards_lease("Failed", FAILURE, conditions=hook_failed),
                tolerate_failed_forwards=True,
            )
        assert not isinstance(excinfo.value, ForwardError)

    async def test_request_that_takes_a_failed_forward_gets_a_forward_that_recovers(self):
        lease = await self._wait(
            forwards_lease("Failed", FAILURE, conditions=FORWARD_FAILED_IN_HOOK),
            forwards_lease("Pending", conditions=FORWARDS_PENDING),
            forwards_lease("Listening"),
            tolerate_failed_forwards=True,
        )
        assert lease.get.await_count == 3 and lease.failed_forwards == {}

    async def test_attach_to_a_lease_with_a_failed_forward_waits_for_the_hooks(self):
        in_hook = forwards_lease("Failed", FAILURE, conditions=FORWARD_FAILED_IN_HOOK)
        failed = forwards_lease("Failed", FAILURE, conditions=FORWARD_FAILED)
        # one read to verify the lease, then the polls
        lease = await self._wait(in_hook, in_hook, in_hook, failed, attach=True)
        assert lease.attached and lease.get.await_count == 4
        assert lease.failed_forwards == {"bt": f"forward 'bt' failed: {FAILURE}"}

    async def test_request_stops_at_once_for_a_failed_forward_also_in_a_hook(self):
        with pytest.raises(ForwardError, match="forward 'bt' failed"):
            await self._wait(forwards_lease("Failed", FAILURE, conditions=FORWARD_FAILED_IN_HOOK))

    async def test_pending_forward_shows_the_forwards_and_not_the_hooks(self):
        statuses: list[str] = []
        record = lambda self, text, **kwargs: statuses.append(text)  # noqa: E731
        with patch("jumpstarter.client.lease.LeaseAcquisitionSpinner.update_status", record):
            await self._wait(
                forwards_lease("Pending", conditions=FORWARD_FAILED_IN_HOOK[:1] + FORWARDS_PENDING[2:]),
                forwards_lease("Pending", conditions=FORWARDS_PENDING),
                forwards_lease("Listening"),
            )
        assert statuses[0] == "Waiting for beforeLease hooks: Waiting for the beforeLease hook of roles: `headunit`"
        assert statuses[1] == "Waiting for forwards: `bt`"

    async def test_request_that_tolerates_forwards_still_stops_for_a_lost_member(self):
        with pytest.raises(LeaseError, match="member 'headunit' lost its exporter") as excinfo:
            await self._wait(
                forwards_lease("Failed", FAILURE, conditions=DEGRADED, statuses=[lost_status()]),
                tolerate_failed_forwards=True,
            )
        assert not isinstance(excinfo.value, ForwardError)

    async def test_ports_of_the_exporters_give_the_directions(self):
        lease = make_lease(name="lease-9m2qd")
        lease.get = AsyncMock(return_value=forwards_lease())
        exporters = {
            "rack3-hu-2": Exporter.from_protobuf(exporter_pb("rack3-hu-2", [("rootcanal", "provides", "hci-h4")])),
            "rack3-phone-4": Exporter.from_protobuf(exporter_pb("rack3-phone-4", [("controller", "requires")])),
        }
        lease.svc.GetExporter = AsyncMock(side_effect=lambda name: exporters[name])
        await lease._acquire()
        assert lease.port_directions() == {("headunit", "rootcanal"): "provides", ("phone", "controller"): "requires"}

    async def test_forward_objects_read_the_lease(self):
        lease = await self._wait(forwards_lease("Listening"))
        lease.get = AsyncMock(return_value=forwards_lease("Failed", FAILURE, conditions=FORWARD_FAILED))
        bt = lease.forward_objects()["bt"]
        assert bt.state == "Listening"  # the read of the acquisition is current
        assert (await bt.refresh_async()).state == "Failed"
        lease.get.assert_awaited_once()
        # the lease keeps what the forward read
        assert lease.failed_forwards == {"bt": f"forward 'bt' failed: {FAILURE}"}
        # the forwards of one lease share one read
        assert lease.forward_objects()["bt"].state == "Failed"


class TestConnectWithForwards:
    """connect() against real exporter sessions, as in the members tests."""

    def _record(self, lease: Any, state="Listening", message=None, forwards=None):
        lease._record(
            lease_model(
                forwards=forwards
                if forwards is not None
                else [forward_pb(), forward_pb("can", "headunit.can", "phone.c")],
                forward_statuses=[forward_status_pb("bt", state, message)],
            )
        )

    def test_members_class_gets_its_forward_and_generic_access_to_all(self):
        with TestConnect()._lease(HeadunitPeer) as lease:
            self._record(lease)
            with lease.connect() as members:
                assert isinstance(members.bt, Forward)
                assert members.bt.name == "bt" and members.bt.state == "Listening"
                assert [str(e) for e in members.bt.endpoints] == ["headunit.rootcanal", "phone.controller"]
                assert members.forwards["bt"] is members.bt
                assert members.forwards["can"].state == "Pending"  # no status yet
                assert members.bt.wait_listening(timeout=1) is members.bt
                members.headunit.power.on()

    def test_template_name_gives_the_forwards_by_name(self):
        with TestConnect()._lease(LeaseMembers) as lease:
            self._record(lease, "Connected")
            with lease.connect() as members:
                assert type(members) is LeaseMembers
                assert set(members.forwards) == {"bt", "can"} and members.forwards["bt"].state == "Connected"

    def test_state_follows_the_lease(self):
        with TestConnect()._lease(HeadunitPeer) as lease:
            self._record(lease, "Listening")
            lease.get = AsyncMock(
                return_value=lease_model(
                    forwards=[forward_pb()], forward_statuses=[forward_status_pb("bt", "Connected")]
                )
            )
            with lease.connect() as members, patch("jumpstarter.client.lease._FORWARD_STATE_MAX_AGE", 0):
                assert members.bt.state == "Connected"

    def test_disabled_forward_raises_from_wait_listening(self):
        with TestConnect()._lease(HeadunitPeer, phone_bound=False) as lease:
            self._record(lease, "Disabled", "member 'phone' is omitted")
            lease.get = AsyncMock(
                return_value=lease_model(
                    forwards=[forward_pb()],
                    forward_statuses=[forward_status_pb("bt", "Disabled", "member 'phone' is omitted")],
                )
            )
            with lease.connect() as members:
                assert members.bt.state == "Disabled"
                with pytest.raises(ForwardError, match="is disabled: member 'phone' is omitted") as excinfo:
                    members.bt.wait_listening(timeout=1)
                assert excinfo.value.state == "Disabled"

    def test_attach_compares_the_class_with_the_forwards_of_the_lease(self):
        with TestConnect()._lease(HeadunitPeer) as lease:
            self._record(lease, forwards=[forward_pb("can")])
            connect: Any = lease.connect()
            with pytest.raises(TypeError, match="declares the forward 'bt', but the lease 'lease-9m2qd' has no such"):
                connect.__enter__()

    def test_lease_without_forwards_gives_an_empty_mapping(self):
        with TestConnect()._lease(LeaseMembers) as lease, lease.connect() as members:
            assert dict(members.forwards) == {}
