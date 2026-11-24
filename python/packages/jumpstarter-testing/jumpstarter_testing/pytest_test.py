import json
from contextlib import contextmanager

from jumpstarter_driver_power.driver import MockPower
from pytest import Pytester

import jumpstarter_testing.pytest as jumpstarter_pytest

from jumpstarter.common import ExporterStatus
from jumpstarter.config.env import JMP_DRIVERS_ALLOW, JMP_LEASE_MEMBERS, JUMPSTARTER_HOST
from jumpstarter.exporter import Session


def test_env(pytester: Pytester, monkeypatch):
    pytester.makepyfile(
        """
        from jumpstarter_testing import JumpstarterTest

        class TestSample(JumpstarterTest):
            def test_simple(self, client):
                client.on()
    """
    )

    with Session(root_device=MockPower()) as session, session.serve_unix() as path:
        # For local testing, set status to LEASE_READY since there's no lease/hook flow
        session.update_status(ExporterStatus.LEASE_READY)
        monkeypatch.setenv(JUMPSTARTER_HOST, str(path))
        monkeypatch.setenv(JMP_DRIVERS_ALLOW, "UNSAFE")
        result = pytester.runpytest()
        result.assert_outcomes(passed=1)


def test_leases_with_the_class_selector(pytester: Pytester, monkeypatch):
    """With no JUMPSTARTER_HOST, the fixture takes a lease using `selector`.

    The selector has to be read off the class: the fixture is class scoped,
    so it runs once while pytest builds a fresh instance for every test.
    Only the lease itself is faked here; the missing environment variable is
    real, so this also pins down which exception sends us down this path.
    """
    leased_with = {}

    class FakeLease:
        @contextmanager
        def connect(self):
            yield MockPower()

    class FakeConfig:
        @contextmanager
        def lease(self, selector):
            leased_with["selector"] = selector
            yield FakeLease()

    class FakeClientConfig:
        @staticmethod
        def load(name):
            leased_with["config"] = name
            return FakeConfig()

    pytester.makepyfile(
        """
        from jumpstarter_testing import JumpstarterTest

        class TestSample(JumpstarterTest):
            selector = "board-type=j784s4evm"

            def test_simple(self, client):
                assert client is not None
    """
    )

    monkeypatch.delenv(JUMPSTARTER_HOST, raising=False)
    monkeypatch.setattr(jumpstarter_pytest, "ClientConfigV1Alpha1", FakeClientConfig)
    result = pytester.runpytest()

    result.assert_outcomes(passed=1)
    assert leased_with == {"config": "default", "selector": "board-type=j784s4evm"}


MEMBERS_TEST = """
    from jumpstarter_driver_power.client import PowerClient
    from jumpstarter_testing import JumpstarterTest

    from jumpstarter.client.lease import LeaseMembers


    class Projection(LeaseMembers, template="projection"):
        pass


    class TestSample(JumpstarterTest):
        members = Projection

        def test_simple(self, client: Projection):
            assert type(client).__name__ == "Projection"
            client.by_role["headunit"].on()
"""


def test_members_class_attaches_to_the_lease_of_the_shell(pytester: Pytester, monkeypatch):
    pytester.makepyfile(MEMBERS_TEST)

    with Session(root_device=MockPower()) as session, session.serve_unix() as path:
        session.update_status(ExporterStatus.LEASE_READY)
        members = {"headunit": {"exporter": "rack3-hu-2", "labels": {}, "socket": str(path)}}
        monkeypatch.delenv(JUMPSTARTER_HOST, raising=False)
        monkeypatch.setenv(JMP_LEASE_MEMBERS, json.dumps(members))
        monkeypatch.setenv(JMP_DRIVERS_ALLOW, "UNSAFE")
        result = pytester.runpytest()
        result.assert_outcomes(passed=1)


DEGRADED_TEST = """
    import pytest
    from jumpstarter_testing import JumpstarterTest

    from jumpstarter.client.exceptions import LeaseMemberLostError
    from jumpstarter.client.lease import LeaseMembers


    class Projection(LeaseMembers, template="projection"):
        pass


    class TestSample(JumpstarterTest):
        members = Projection

        def test_bound_role_works(self, client: Projection):
            client.by_role["headunit"].on()

        def test_lost_role_names_the_role_and_the_reason(self, client: Projection):
            with pytest.raises(LeaseMemberLostError, match="role 'phone' is lost: ExporterReleased"):
                client.by_role["phone"].on()
            assert client.metadata["phone"].name == "rack3-phone-4"
"""


def test_members_class_attaches_to_a_degraded_lease_of_the_shell(pytester: Pytester, monkeypatch):
    pytester.makepyfile(DEGRADED_TEST)

    with Session(root_device=MockPower()) as session, session.serve_unix() as path:
        session.update_status(ExporterStatus.LEASE_READY)
        members = {
            "headunit": {"exporter": "rack3-hu-2", "labels": {}, "socket": str(path)},
            "phone": {
                "exporter": "rack3-phone-4",
                "labels": {},
                "socket": None,
                "lost": {"reason": "ExporterReleased", "message": ""},
            },
        }
        monkeypatch.delenv(JUMPSTARTER_HOST, raising=False)
        monkeypatch.setenv(JMP_LEASE_MEMBERS, json.dumps(members))
        monkeypatch.setenv(JMP_DRIVERS_ALLOW, "UNSAFE")
        result = pytester.runpytest()
        result.assert_outcomes(passed=2)


def test_members_class_requests_its_template_outside_a_shell(pytester: Pytester, monkeypatch):
    leased_with = {}

    class FakeLease:
        @contextmanager
        def connect(self):
            yield leased_with["members"]({"headunit": MockPowerClient()}, {"headunit": None})

    class FakeConfig:
        @contextmanager
        def lease(self, members):
            leased_with["members"] = members
            yield FakeLease()

    class FakeClientConfig:
        @staticmethod
        def load(name):
            leased_with["config"] = name
            return FakeConfig()

    class MockPowerClient:
        children: dict = {}  # noqa: RUF012
        labels: dict = {}  # noqa: RUF012

        def on(self):
            leased_with["called"] = True

    pytester.makepyfile(MEMBERS_TEST)
    monkeypatch.delenv(JUMPSTARTER_HOST, raising=False)
    monkeypatch.delenv(JMP_LEASE_MEMBERS, raising=False)
    monkeypatch.setattr(jumpstarter_pytest, "ClientConfigV1Alpha1", FakeClientConfig)
    result = pytester.runpytest()

    result.assert_outcomes(passed=1)
    assert leased_with["config"] == "default"
    assert leased_with["members"].__lease_template__ == "projection"
    assert leased_with["called"]


def test_selector_test_in_a_members_shell_fails_with_the_hint(pytester: Pytester, monkeypatch):
    pytester.makepyfile(
        """
        from jumpstarter_testing import JumpstarterTest

        class TestSample(JumpstarterTest):
            selector = "board=rpi"

            def test_simple(self, client):
                pass
    """
    )
    monkeypatch.delenv(JUMPSTARTER_HOST, raising=False)
    monkeypatch.setenv(JMP_LEASE_MEMBERS, json.dumps({"headunit": None}))
    result = pytester.runpytest()
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*env_members()*"])
