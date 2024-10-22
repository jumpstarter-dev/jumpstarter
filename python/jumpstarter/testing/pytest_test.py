from jumpstarter.config.env import JMP_DRIVERS_ALLOW, JUMPSTARTER_HOST
from jumpstarter.drivers.power.driver import MockPower
from jumpstarter.exporter import Session


def test_env(pytester, monkeypatch):
    pytester.makepyfile(
        """
        from jumpstarter.testing.pytest import JumpstarterTest

        class TestSample(JumpstarterTest):
            def test_simple(self, client):
                assert client.on() == "ok"
    """
    )

    with Session(root_device=MockPower()) as session:
        with session.serve_unix() as path:
            monkeypatch.setenv(JUMPSTARTER_HOST, str(path))
            monkeypatch.setenv(JMP_DRIVERS_ALLOW, "UNSAFE")
            result = pytester.runpytest()
            result.assert_outcomes(passed=1)
