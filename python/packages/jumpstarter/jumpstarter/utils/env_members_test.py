import json
import os
from contextlib import ExitStack, contextmanager
from typing import Any

import pytest
from jumpstarter_driver_composite.client import CompositeClient
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_network.client import NetworkClient
from jumpstarter_driver_power.client import PowerClient
from jumpstarter_driver_power.driver import MockPower

from jumpstarter.client.exceptions import LeaseMemberLostError
from jumpstarter.client.lease import LeaseMembers, LostMember
from jumpstarter.common import ExporterStatus
from jumpstarter.common.exceptions import EnvironmentVariableNotSetError
from jumpstarter.common.utils import _build_members_env
from jumpstarter.config.env import (
    JMP_DRIVERS_ALLOW,
    JMP_EXPORTER,
    JMP_EXPORTER_LABELS,
    JMP_LEASE,
    JMP_LEASE_MEMBERS,
    JUMPSTARTER_HOST,
)
from jumpstarter.exporter import Session
from jumpstarter.utils.env import ExporterMetadata, env, env_members, env_with_metadata


class Headunit(CompositeClient):
    power: PowerClient


class Projection(LeaseMembers, template="projection"):
    phone: Headunit | None
    headunit: Headunit


@contextmanager
def members_shell(monkeypatch, *, phone=True, headunit_lost=False):
    """The environment of `jmp shell` on a lease with members, over local exporter sessions."""
    with ExitStack() as stack:
        members: dict[str, dict | None] = {}
        for role in ("phone", "headunit"):
            if role == "phone" and not phone:
                members[role] = None
                continue
            if role == "headunit" and headunit_lost:
                members[role] = {
                    "exporter": "rack3-headunit",
                    "labels": {"device-type": role},
                    "socket": None,
                    "lost": {"reason": "Offline", "message": "no heartbeat for 60s"},
                }
                continue
            session = stack.enter_context(Session(root_device=Composite(children={"power": MockPower()})))
            path = stack.enter_context(session.serve_unix())
            session.update_status(ExporterStatus.LEASE_READY)
            members[role] = {"exporter": f"rack3-{role}", "labels": {"device-type": role}, "socket": str(path)}
        monkeypatch.delenv(JUMPSTARTER_HOST, raising=False)
        monkeypatch.setenv(JMP_LEASE, "lease-9m2qd")
        monkeypatch.setenv(JMP_LEASE_MEMBERS, json.dumps(members))
        monkeypatch.setenv(JMP_DRIVERS_ALLOW, "UNSAFE")
        yield members


@pytest.mark.parametrize("function", [env, env_with_metadata])
def test_env_fails_in_a_members_shell_and_names_env_members(monkeypatch, function):
    with members_shell(monkeypatch), pytest.raises(
        EnvironmentVariableNotSetError, match="lease with members \\(phone, headunit\\).*env_members\\(\\)"
    ), function():
        pass


def test_env_without_any_shell_keeps_its_message(monkeypatch):
    monkeypatch.delenv(JUMPSTARTER_HOST, raising=False)
    monkeypatch.delenv(JMP_LEASE_MEMBERS, raising=False)
    with pytest.raises(EnvironmentVariableNotSetError, match="^JUMPSTARTER_HOST not set$"), env():
        pass


def test_env_members_gives_the_members_with_metadata(monkeypatch):
    with members_shell(monkeypatch), env_members() as members:
        assert type(members) is LeaseMembers
        assert set(members.by_role) == {"phone", "headunit"}
        headunit: Any = members.by_role["headunit"]
        headunit.power.on()
        assert members.metadata["phone"] == ExporterMetadata(
            name="rack3-phone", labels={"device-type": "phone"}, lease="lease-9m2qd"
        )


def test_env_members_with_a_class_gives_a_typed_instance(monkeypatch):
    with members_shell(monkeypatch, phone=False), env_members(Projection) as members:
        assert type(members) is Projection
        assert members.phone is None and members.metadata["phone"] is None
        assert isinstance(members.headunit, Headunit)
        members.headunit.power.off()


def test_env_members_checks_the_class_against_the_shell(monkeypatch):
    class Other(LeaseMembers):
        ecu: Headunit

    class Strict(LeaseMembers):
        phone: Headunit
        headunit: Headunit

    class WrongDriver(CompositeClient):
        power: NetworkClient

    class Wrong(LeaseMembers):
        headunit: WrongDriver

    with members_shell(monkeypatch, phone=False):
        with pytest.raises(TypeError, match="Other declares the role 'ecu', but the lease of this shell has no"):  # noqa: SIM117
            with env_members(Other):
                pass
        with pytest.raises(TypeError, match="role 'phone' is optional in the lease of this shell"):  # noqa: SIM117
            with env_members(Strict):
                pass
        with pytest.raises(TypeError, match="Wrong: role 'headunit': driver 'power' is"), env_members(Wrong):
            pass


def test_env_members_outside_a_members_shell(monkeypatch):
    monkeypatch.delenv(JMP_LEASE_MEMBERS, raising=False)
    with pytest.raises(EnvironmentVariableNotSetError, match="JMP_LEASE_MEMBERS not set"), env_members():
        pass


def test_members_shell_environment_removes_the_variables_of_one_exporter(monkeypatch):
    monkeypatch.setenv(JUMPSTARTER_HOST, "/outer/socket")
    monkeypatch.setenv(JMP_EXPORTER, "outer")
    monkeypatch.setenv(JMP_EXPORTER_LABELS, "a=b")
    members = {
        "phone": {"exporter": "rack3-phone-4", "labels": {"device-type": "phone"}, "socket": "/x/phone.sock"},
        "headunit": None,
    }

    class FakeLease:
        name = "lease-9m2qd"

    environment = _build_members_env(members, [], True, lease=FakeLease())
    assert JUMPSTARTER_HOST not in environment
    assert JMP_EXPORTER not in environment
    assert JMP_EXPORTER_LABELS not in environment
    assert environment[JMP_LEASE] == "lease-9m2qd"
    assert json.loads(environment[JMP_LEASE_MEMBERS]) == members
    assert environment[JMP_DRIVERS_ALLOW] == "UNSAFE"


def test_members_shell_environment_applies_extra_env_last(monkeypatch):
    monkeypatch.setenv("JMP_OUTER", "outer")
    before = dict(os.environ)
    environment = _build_members_env(
        {"phone": None}, [], True, extra_env={"JMP_OUTER": None, "JMP_ABSENT": None, JMP_LEASE_MEMBERS: "{}"}
    )
    assert "JMP_OUTER" not in environment and "JMP_ABSENT" not in environment
    assert environment[JMP_LEASE_MEMBERS] == "{}"
    # the environment of this process does not change
    assert dict(os.environ) == before


def test_env_members_gives_a_raising_object_for_a_lost_role(monkeypatch):
    with members_shell(monkeypatch, headunit_lost=True), env_members(Projection) as members:
        assert members.phone is not None
        members.phone.power.on()
        assert isinstance(members.headunit, LostMember)
        with pytest.raises(
            LeaseMemberLostError,
            match="^role 'headunit' is lost: Offline: no heartbeat for 60s \\(exporter rack3-headunit\\)$",
        ) as excinfo:
            members.headunit.power.on()
        assert excinfo.value.role == "headunit" and excinfo.value.reason == "Offline"
        assert members.metadata["headunit"] == ExporterMetadata(
            name="rack3-headunit", labels={"device-type": "headunit"}, lease="lease-9m2qd"
        )
        assert isinstance(members.by_role["headunit"], LostMember)


def test_env_member_of_a_lost_role_names_the_role_and_the_reason(monkeypatch):
    from anyio.from_thread import start_blocking_portal

    from jumpstarter.utils.env import env_member_async

    with members_shell(monkeypatch, headunit_lost=True), start_blocking_portal() as portal, ExitStack() as stack:
        with pytest.raises(LeaseMemberLostError, match="role 'headunit' is lost: Offline"):  # noqa: SIM117
            with portal.wrap_async_context_manager(env_member_async("headunit", portal, stack)):
                pass
        with portal.wrap_async_context_manager(env_member_async("phone", portal, stack)) as phone:
            phone.power.on()
