from jumpstarter_driver_power.driver import MockPower
from pytest import Pytester

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

    with Session(root_device=MockPower()) as session:
        with session.serve_unix() as path:
            monkeypatch.setenv(JUMPSTARTER_HOST, str(path))
            monkeypatch.setenv(JMP_DRIVERS_ALLOW, "UNSAFE")
            result = pytester.runpytest()
            result.assert_outcomes(passed=1)
