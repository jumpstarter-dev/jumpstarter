"""`jmp shell` on a lease with forwards (JEP-0016)."""

import json
import os
from datetime import timedelta
from unittest.mock import patch

import anyio
import click
import pytest

from jumpstarter_cli.members import MemberRequest
from jumpstarter_cli.shell import (
    _exported_forwards,
    _forward_line,
    _forwards_environment,
    _members_shell_with_signal_handling,
    _monitor_lost_members,
)
from jumpstarter_cli.shell_members_test import FakeConfig, FakeLease, run_shell

from jumpstarter.client._members_testing import forward_pb, forward_status_pb, members_lease_pb
from jumpstarter.client.grpc import ForwardEndpoint, Lease, LeaseForward
from jumpstarter.config.env import JMP_LEASE_MEMBERS
from jumpstarter.utils.env import JMP_LEASE_FORWARDS

FAILURE = "port 'rootcanal' is not on exporter rack3-hu-2"
BT_ENVIRONMENT = {
    "name": "bt",
    "endpoints": [
        {"member": "headunit", "port": "rootcanal", "direction": "provides"},
        {"member": "phone", "port": "controller", "direction": "requires"},
    ],
    "state": "Listening",
    "message": None,
}


class ForwardsLease(FakeLease):
    """What the shell uses of a MembersLease with forwards."""

    def __init__(self, state="Listening", message=None, directions=True):
        super().__init__()
        self._model = Lease.from_protobuf(
            members_lease_pb(
                forwards=[forward_pb(), forward_pb("can", "headunit.can", "phone.can-remote")],
                forward_statuses=[forward_status_pb("bt", state, message)],
            )
        )
        self.lease_forwards = list(self._model.forwards)
        self.forward_statuses = {status.name: status for status in self._model.forward_statuses}
        self.failed_forwards = {s.name: s.describe_failure() for s in self._model.failed_forwards()}
        self._directions = (
            {("headunit", "rootcanal"): "provides", ("phone", "controller"): "requires"} if directions else {}
        )

    def port_directions(self):
        return self._directions

    async def get(self):
        return self._model


@pytest.fixture(autouse=True)
def no_outer_forwards(monkeypatch):
    monkeypatch.delenv(JMP_LEASE_FORWARDS, raising=False)


def run_session(lease, command=("true",), request=None):
    """Run the members shell and return the environment of its child process."""
    config = FakeConfig(lease=lease)
    seen = {}

    def run_process(cmd, env, lease=None):
        seen.update(env=env)
        return 0

    with patch("jumpstarter.common.utils._run_process", run_process):
        exit_code = anyio.run(
            _members_shell_with_signal_handling, config, request, None, timedelta(hours=1), False, command, None
        )
    assert exit_code == 0
    return config, seen["env"]


class TestForwardOption:
    def test_forward_goes_into_the_members_request(self):
        config = FakeConfig()
        seen: dict = {}
        with (
            patch("jumpstarter_cli.shell._members_shell", side_effect=lambda *args: seen.update(request=args[1]) or 0),
            patch("jumpstarter_cli.shell.sys.exit", side_effect=SystemExit),
            patch("jumpstarter_cli.shell.ClientConfigV1Alpha1", FakeConfig),
            pytest.raises(SystemExit),
        ):
            run_shell(
                config,
                member_entries=("peer=device-type=bt-peer", "headunit=device-type=aaos-headunit"),
                forward_entries=("bt=headunit.rootcanal,peer.controller",),
            )
        assert seen["request"].forwards == [
            LeaseForward(
                name="bt",
                endpoints=[
                    ForwardEndpoint(member_name="headunit", port_name="rootcanal"),
                    ForwardEndpoint(member_name="peer", port_name="controller"),
                ],
            )
        ]

    @pytest.mark.parametrize(
        "kwargs,message",
        [
            ({"selector": "a=b"}, "--forward needs --member"),
            ({"lease_name": "lease-9m2qd"}, "--forward needs --member"),
            ({"lease_template": "ivi"}, "--forward cannot be used with --lease-template/-t"),
            ({"member_entries": ("peer=a=b", "headunit=c=d"), "lease_name": "x"}, "--lease attaches to a lease"),
            ({"member_entries": ("peer=a=b", "headunit=c=d"), "tls_grpc_address": "h:1"}, "cannot be used with"),
            ({"member_entries": ("peer=a=b",)}, "no --member defines the role 'headunit'"),
        ],
    )
    def test_rejected_combinations_start_no_shell(self, kwargs, message):
        config = FakeConfig()
        with (
            patch("jumpstarter_cli.shell._members_shell") as members_shell,
            patch("jumpstarter_cli.shell.ClientConfigV1Alpha1", FakeConfig),
            pytest.raises(click.UsageError, match=message),
        ):
            run_shell(config, forward_entries=("bt=headunit.rootcanal,peer.controller",), **kwargs)
        members_shell.assert_not_called()

    def test_request_passes_its_forwards_and_tolerates_a_failed_one(self):
        request = MemberRequest(
            members=[],
            forwards=[
                LeaseForward(
                    name="bt",
                    endpoints=[
                        ForwardEndpoint(member_name="headunit", port_name="rootcanal"),
                        ForwardEndpoint(member_name="peer", port_name="controller"),
                    ],
                )
            ],
        )
        config, _ = run_session(FakeLease(), request=request)
        assert config.requested["forwards"] == request.forwards
        # jmp shell opens the shell on a lease with a failed forward
        assert config.requested["tolerate_failed_forwards"] is True

    def test_attach_tolerates_a_failed_forward_too(self):
        config, _ = run_session(FakeLease())
        assert config.requested["tolerate_failed_forwards"] is True and "forwards" not in config.requested


class TestEnvironment:
    def test_forwards_with_endpoints_directions_and_state(self):
        assert _forwards_environment(ForwardsLease()) == [
            BT_ENVIRONMENT,
            {
                "name": "can",
                # the exporters do not report these ports: no direction
                "endpoints": [{"member": "headunit", "port": "can"}, {"member": "phone", "port": "can-remote"}],
                "state": "Pending",
                "message": None,
            },
        ]

    def test_failed_forward_has_its_cause(self):
        assert _forwards_environment(ForwardsLease("Failed", FAILURE))[0]["message"] == FAILURE

    def test_lease_without_forwards(self):
        assert _forwards_environment(FakeLease()) == []

    def test_child_gets_the_forwards_next_to_the_members(self):
        _, env = run_session(ForwardsLease())
        assert json.loads(env[JMP_LEASE_FORWARDS])[0] == BT_ENVIRONMENT
        assert set(json.loads(env[JMP_LEASE_MEMBERS])) == {"phone", "headunit", "tablet"}
        # the environment of jmp itself is as it was
        assert JMP_LEASE_FORWARDS not in os.environ

    def test_lease_without_forwards_removes_the_variable_of_an_outer_shell(self, monkeypatch):
        monkeypatch.setenv(JMP_LEASE_FORWARDS, json.dumps([BT_ENVIRONMENT]))
        _, env = run_session(FakeLease())
        assert JMP_LEASE_FORWARDS not in env
        assert json.loads(os.environ[JMP_LEASE_FORWARDS]) == [BT_ENVIRONMENT]

    def test_exported_forwards_restores_the_environment(self, monkeypatch):
        monkeypatch.setenv(JMP_LEASE_FORWARDS, "outer")
        with _exported_forwards([BT_ENVIRONMENT]):
            assert json.loads(os.environ[JMP_LEASE_FORWARDS]) == [BT_ENVIRONMENT]
        assert os.environ[JMP_LEASE_FORWARDS] == "outer"


class TestShellStart:
    def test_forwards_are_listed_after_the_members(self, capsys):
        run_session(ForwardsLease("Connected"), command=())
        err = capsys.readouterr().err
        assert "  phone: rack3-phone-4" in err
        assert "  forward bt: headunit.rootcanal → phone.controller (Connected)" in err
        assert "  forward can: headunit.can, phone.can-remote (Pending)" in err
        assert "Warning" not in err

    def test_failed_forward_is_a_warning_and_the_shell_opens(self, capsys):
        _, env = run_session(ForwardsLease("Failed", FAILURE), command=())
        err = capsys.readouterr().err
        assert f"  forward bt: headunit.rootcanal → phone.controller (Failed): {FAILURE}" in err
        assert f"Warning: forward 'bt' failed: {FAILURE}. The members stay usable." in err
        assert "j-forward status" in err
        assert json.loads(env[JMP_LEASE_FORWARDS])[0]["state"] == "Failed"

    def test_command_gets_the_warning_without_the_list(self, capsys):
        run_session(ForwardsLease("Failed", FAILURE), command=("true",))
        err = capsys.readouterr().err
        assert "Warning: forward 'bt' failed" in err and "  forward bt:" not in err

    def test_disabled_forward_shows_its_cause_and_is_no_warning(self, capsys):
        run_session(ForwardsLease("Disabled", "member 'phone' is omitted"), command=())
        err = capsys.readouterr().err
        assert "(Disabled): member 'phone' is omitted" in err and "Warning" not in err

    def test_forward_line_of_a_path(self):
        line = _forward_line(
            {
                "name": "uart",
                "endpoints": [
                    {"member": "dut", "port": "uart", "direction": "connects"},
                    {"member": "companion", "port": "uart", "direction": "connects"},
                ],
                "state": "Listening",
            }
        )
        assert line == "  forward uart: dut.uart ↔ companion.uart (Listening)"


class TestMonitor:
    def _monitor(self, lease):
        async def main():
            scope = anyio.CancelScope()
            with patch("jumpstarter_cli.shell._SHARED_ACCESS_POLL_SECONDS", 0.01), scope:
                with anyio.move_on_after(0.2):
                    await _monitor_lost_members(lease, scope)

        anyio.run(main)

    def test_forward_that_fails_during_the_session_is_reported_once(self, capsys):
        lease = ForwardsLease("Listening")
        lease._model = ForwardsLease("Failed", FAILURE)._model
        self._monitor(lease)
        out = capsys.readouterr().out
        assert out.count(f"Lease lease-9m2qd: forward 'bt' failed: {FAILURE}") == 1
        assert "The members stay usable" in out and "j-forward status" in out

    def test_forward_that_had_failed_at_the_start_is_not_reported_again(self, capsys):
        self._monitor(ForwardsLease("Failed", FAILURE))
        assert "failed" not in capsys.readouterr().out

    def test_lease_without_forwards_reports_nothing(self, capsys):
        self._monitor(FakeLease())
        assert capsys.readouterr().out == ""
