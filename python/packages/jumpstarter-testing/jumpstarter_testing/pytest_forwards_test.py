"""`JumpstarterTest` with a members class that declares a forward (JEP-0016)."""

import json

from jumpstarter_driver_power.driver import MockPower
from pytest import Pytester

from jumpstarter.common import ExporterStatus
from jumpstarter.config.env import JMP_DRIVERS_ALLOW, JMP_LEASE, JMP_LEASE_MEMBERS, JUMPSTARTER_HOST
from jumpstarter.exporter import Session
from jumpstarter.utils.env import JMP_LEASE_FORWARDS

FORWARDS_TEST = """
    import pytest
    from jumpstarter_testing import JumpstarterTest

    from jumpstarter.client.exceptions import ForwardError
    from jumpstarter.client.lease import Forward, LeaseMembers


    class HeadunitPeer(LeaseMembers, template="headunit-peer"):
        bt: Forward


    class TestSample(JumpstarterTest):
        members = HeadunitPeer

        def test_forward_of_the_class(self, client: HeadunitPeer):
            assert isinstance(client.bt, Forward) and client.bt.name == "bt"
            assert client.bt.wait_listening(timeout=1) is client.bt
            client.by_role["headunit"].on()

        def test_forward_that_the_class_does_not_declare(self, client: HeadunitPeer):
            assert client.forwards["can"].state == "Failed"
            with pytest.raises(ForwardError, match="the listen address is in use"):
                client.forwards["can"].wait_listening(timeout=1)
"""

FORWARDS = [
    {
        "name": "bt",
        "endpoints": [{"member": "headunit", "port": "rootcanal"}, {"member": "peer", "port": "controller"}],
        "state": "Listening",
        "message": None,
    },
    {
        "name": "can",
        "endpoints": [{"member": "headunit", "port": "can"}, {"member": "peer", "port": "can-remote"}],
        "state": "Failed",
        "message": "the listen address is in use",
    },
]


def run_in_members_shell(pytester: Pytester, monkeypatch, forwards):
    pytester.makepyfile(FORWARDS_TEST)
    # No client config: the forwards keep the states of the environment of the shell.
    pytester.makeconftest(
        """
        import pytest

        @pytest.fixture(autouse=True, scope="session")
        def no_client_config():
            import jumpstarter.utils.env as env

            env._current_client_config = lambda: None
            yield
        """
    )
    with Session(root_device=MockPower()) as session, session.serve_unix() as path:
        session.update_status(ExporterStatus.LEASE_READY)
        members = {"headunit": {"exporter": "rack3-hu-2", "labels": {}, "socket": str(path)}}
        monkeypatch.delenv(JUMPSTARTER_HOST, raising=False)
        monkeypatch.setenv(JMP_LEASE, "lease-9m2qd")
        monkeypatch.setenv(JMP_LEASE_MEMBERS, json.dumps(members))
        monkeypatch.setenv(JMP_DRIVERS_ALLOW, "UNSAFE")
        if forwards is None:
            monkeypatch.delenv(JMP_LEASE_FORWARDS, raising=False)
        else:
            monkeypatch.setenv(JMP_LEASE_FORWARDS, json.dumps(forwards))
        return pytester.runpytest()


def test_members_class_gets_the_forwards_of_the_lease_of_the_shell(pytester: Pytester, monkeypatch):
    result = run_in_members_shell(pytester, monkeypatch, FORWARDS)
    result.assert_outcomes(passed=2)


def test_members_class_with_a_forward_that_the_lease_does_not_contain(pytester: Pytester, monkeypatch):
    result = run_in_members_shell(pytester, monkeypatch, None)
    result.assert_outcomes(errors=2)
    result.stdout.fnmatch_lines(["*HeadunitPeer declares the forward 'bt', but the lease of this shell has no such*"])
