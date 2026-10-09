from contextlib import contextmanager

from jumpstarter_driver_power.driver import MockPower
from pytest import Pytester

import jumpstarter_testing.pytest as jumpstarter_pytest

from jumpstarter.common import ExporterStatus
from jumpstarter.config.env import JMP_DRIVERS_ALLOW, JUMPSTARTER_HOST
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
