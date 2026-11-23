import inspect
import json
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from unittest.mock import Mock, patch

import anyio
import click
import pytest
from click.testing import CliRunner
from jumpstarter_protocol import client_pb2

from jumpstarter_cli.jmp import jmp
from jumpstarter_cli.members import MemberRequest
from jumpstarter_cli.shell import (
    _format_lease_display,
    _is_member_form_lease,
    _members_environment,
    _members_shell,
    _members_shell_with_signal_handling,
    _monitor_lost_members,
    shell,
)

from jumpstarter.client.exceptions import LeaseError
from jumpstarter.client.grpc import Lease, LeaseTemplate
from jumpstarter.client.grpc_members_test import members_lease_pb, template_pb
from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter.config.env import (
    JMP_EXPORTER,
    JMP_EXPORTER_LABELS,
    JMP_LEASE,
    JMP_LEASE_MEMBERS,
    JUMPSTARTER_HOST,
)


def lost_status():
    return client_pb2.LeaseMemberStatus(
        name="headunit",
        state=client_pb2.LEASE_MEMBER_STATE_LOST,
        exporter="namespaces/default/exporters/rack3-headunit",
        reason="Offline",
    )


def run_shell(config, **kwargs):
    defaults = {
        "config": config,
        "command": (),
        "lease_name": None,
        "selector": None,
        "exporter_name": None,
        "duration": timedelta(minutes=30),
        "exporter_logs": False,
        "allow_disabled": False,
        "acquisition_timeout": None,
        "retry_timeout": None,
        "dial_timeout": None,
        "tls_grpc_address": None,
        "tls_grpc_insecure": False,
        "passphrase": None,
    }
    assert shell.callback is not None
    inspect.unwrap(shell.callback)(**{**defaults, **kwargs})


class FakeLease:
    """What the shell uses of a MembersLease."""

    name = "lease-9m2qd"
    release = True
    client_name = None
    allow: list = []  # noqa: RUF012
    unsafe = True
    portal = None
    lease_revoked = False
    lease_ending_callback = None

    def __init__(self):
        self.lost = {}
        self.member_labels = {"phone": {"device-type": "phone"}}
        self.released = False

    def member_exporter(self, role):
        return {"phone": "rack3-phone-4", "headunit": "rack3-hu-2"}.get(role)

    @asynccontextmanager
    async def serve_unix_async(self):
        yield {
            "phone": "/tmp/jumpstarter-x1/phone.sock",
            "headunit": "/tmp/jumpstarter-x1/headunit.sock",
            "tablet": None,
        }

    @asynccontextmanager
    async def monitor_async(self):
        yield

    async def get(self):
        return Lease.from_protobuf(members_lease_pb())


class FakeConfig:
    token = None

    def __init__(self, lease=None, error=None):
        self.lease = lease or FakeLease()
        self.error = error
        self.drivers = Mock(allow=["jumpstarter_driver_power.*"], unsafe=False)
        self.shell = Mock(use_profiles=False)
        self.requested: dict = {}

    @asynccontextmanager
    async def lease_members_async(self, **kwargs):
        self.requested = kwargs
        if self.error is not None:
            raise self.error
        try:
            yield self.lease
        finally:
            self.lease.released = True


class TestShellCommand:
    def _config(self):
        config = Mock(spec=ClientConfigV1Alpha1)
        config.get_lease_template.return_value = LeaseTemplate.from_protobuf(template_pb())
        return config

    def test_template_starts_the_members_shell(self):
        config = self._config()
        with (
            patch("jumpstarter_cli.shell._members_shell", return_value=0) as members_shell,
            patch("jumpstarter_cli.shell.sys.exit", side_effect=SystemExit) as exit_,
            pytest.raises(SystemExit),
        ):
            run_shell(config, lease_template="ivi", without=("phone",), member_entries=("headunit=soc=x",))
        request = members_shell.call_args.args[1]
        assert request == MemberRequest(
            template="ivi", excluded_members=["phone"], member_selectors={"headunit": "soc=x"}
        )
        assert members_shell.call_args.args[2] is None
        exit_.assert_called_once_with(0)

    def test_lease_of_a_lease_with_members_starts_the_members_shell(self):
        config = self._config()
        config.get_lease.return_value = Lease.from_protobuf(members_lease_pb())
        with (
            patch("jumpstarter_cli.shell._members_shell", return_value=0) as members_shell,
            patch("jumpstarter_cli.shell.sys.exit", side_effect=SystemExit),
            pytest.raises(SystemExit),
        ):
            run_shell(config, lease_name="lease-9m2qd")
        config.get_lease.assert_called_once_with(name="lease-9m2qd")
        assert members_shell.call_args.args[1] is None
        assert members_shell.call_args.args[2] == "lease-9m2qd"

    def test_lease_of_one_exporter_keeps_the_usual_shell(self):
        config = self._config()
        scalar = Lease.from_protobuf(members_lease_pb())
        scalar.members = []
        config.get_lease.return_value = scalar
        with (
            patch("jumpstarter_cli.shell._members_shell") as members_shell,
            patch("jumpstarter_cli.shell.anyio.run", return_value=0),
            patch("jumpstarter_cli.shell.sys.exit"),
        ):
            run_shell(config, lease_name="one")
        members_shell.assert_not_called()

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"lease_template": "ivi", "selector": "a=b"}, "--selector/-l cannot be used with --lease-template/-t"),
            ({"lease_template": "ivi", "exporter_name": "e"}, "--name/-n cannot be used with --lease-template/-t"),
            ({"member_entries": ("a=b=c",), "selector": "a=b"}, "--selector/-l cannot be used with --member"),
            ({"without": ("phone",)}, "--without needs --lease-template/-t"),
            ({"lease_template": "ivi", "same_label_entries": ("k=a,b",)}, "--same-label cannot be used with"),
            ({"lease_template": "ivi", "lease_name": "x"}, "--lease attaches to a lease that exists"),
            ({"lease_template": "ivi", "tls_grpc_address": "h:1"}, "cannot be used with --tls-grpc"),
        ],
    )
    def test_rejected_combinations(self, kwargs, message):
        with pytest.raises(click.UsageError, match=message):
            run_shell(self._config(), **kwargs)

    def test_command_line_has_the_options(self):
        result = CliRunner().invoke(jmp, ["shell", "--help"])
        for option in (
            "-t, --lease-template NAME",
            "--without ROLE",
            "--member ROLE=SELECTOR",
            "--same-label KEY=ROLE,ROLE",
        ):
            assert option in result.output

    def test_is_member_form_lease_answers_no_on_any_failure(self):
        config = Mock()
        config.get_lease.side_effect = RuntimeError("boom")
        assert not _is_member_form_lease(config, "x")
        assert not _is_member_form_lease(Mock(), "x")  # a mock lease has no list of members
        config = Mock()
        config.get_lease.return_value = Lease.from_protobuf(members_lease_pb())
        assert _is_member_form_lease(config, "x")


class TestDuration:
    def _run(self, template, duration=timedelta(minutes=30)):
        config = Mock()
        config.get_lease_template.return_value = template
        with patch("jumpstarter_cli.shell.anyio.run", return_value=0) as run:
            _members_shell(config, MemberRequest(template="ivi"), None, duration, False, (), None, None, None)
        return run.call_args.args[4]

    def test_template_default_applies_when_duration_is_not_given(self):
        assert self._run(LeaseTemplate.from_protobuf(template_pb())) is None

    def test_shell_default_applies_when_the_template_has_none(self):
        definition = template_pb()
        definition.ClearField("default_duration")
        assert self._run(LeaseTemplate.from_protobuf(definition)) == timedelta(minutes=30)

    def test_duration_from_the_command_line_wins(self):
        ctx = click.Context(shell)
        ctx.set_parameter_source("duration", click.core.ParameterSource.COMMANDLINE)
        with ctx:
            assert self._run(LeaseTemplate.from_protobuf(template_pb()), timedelta(hours=4)) == timedelta(hours=4)

    def test_template_role_errors_stop_before_the_request(self):
        config = Mock()
        config.get_lease_template.return_value = LeaseTemplate.from_protobuf(template_pb())
        with patch("jumpstarter_cli.shell.anyio.run") as run:  # noqa: SIM117
            with pytest.raises(click.UsageError, match="the role 'headunit' is required"):
                _members_shell(
                    config,
                    MemberRequest(template="ivi", excluded_members=["headunit"]),
                    None,
                    None,
                    False,
                    (),
                    None,
                    None,
                    None,
                )
        run.assert_not_called()


class TestMembersSession:
    def test_environment_has_exporter_labels_and_socket_of_each_role(self):
        lease = FakeLease()
        assert _members_environment(lease, {"phone": "/x/phone.sock", "tablet": None}) == {
            "phone": {"exporter": "rack3-phone-4", "labels": {"device-type": "phone"}, "socket": "/x/phone.sock"},
            "tablet": None,
        }

    def test_shell_gets_the_members_and_not_the_variables_of_one_exporter(self, monkeypatch):
        monkeypatch.setenv(JUMPSTARTER_HOST, "/outer/socket")
        monkeypatch.setenv(JMP_EXPORTER, "outer")
        monkeypatch.setenv(JMP_EXPORTER_LABELS, "a=b")
        config = FakeConfig()
        request = MemberRequest(template="ivi", excluded_members=["phone"])
        seen = {}

        def run_process(cmd, env, lease=None):
            seen.update(cmd=cmd, env=env, lease=lease)
            return 7

        with patch("jumpstarter.common.utils._run_process", run_process):
            exit_code = anyio.run(
                _members_shell_with_signal_handling, config, request, None, timedelta(hours=1), False, ("true",), None
            )

        assert exit_code == 7
        assert config.requested["template"] == "ivi" and config.requested["excluded_members"] == ["phone"]
        assert config.requested["duration"] == timedelta(hours=1) and config.requested["lease_name"] is None
        env = seen["env"]
        assert seen["cmd"] == ["true"]
        assert env[JMP_LEASE] == "lease-9m2qd"
        assert json.loads(env[JMP_LEASE_MEMBERS]) == {
            "phone": {
                "exporter": "rack3-phone-4",
                "labels": {"device-type": "phone"},
                "socket": "/tmp/jumpstarter-x1/phone.sock",
            },
            "headunit": {"exporter": "rack3-hu-2", "labels": {}, "socket": "/tmp/jumpstarter-x1/headunit.sock"},
            "tablet": None,
        }
        for removed in (JUMPSTARTER_HOST, JMP_EXPORTER, JMP_EXPORTER_LABELS):
            assert removed not in env
        assert seen["lease"] is config.lease  # the lease ending callback ends the shell
        assert config.lease.released

    def test_attach_passes_only_the_lease_name(self):
        config = FakeConfig()
        with patch("jumpstarter_cli.shell.launch_members_shell", return_value=0) as launch:
            anyio.run(_members_shell_with_signal_handling, config, None, "lease-9m2qd", None, False, (), None)
        assert config.requested["lease_name"] == "lease-9m2qd"
        assert "template" not in config.requested and "members" not in config.requested
        assert launch.call_args.args[1] == "lease-9m2qd"  # the prompt names the lease

    def test_lease_error_reaches_the_user(self):
        config = FakeConfig(error=LeaseError("lease x: member 'phone' lost its exporter"))
        with pytest.raises(BaseExceptionGroup) as excinfo:
            anyio.run(
                _members_shell_with_signal_handling, config, MemberRequest(template="ivi"), None, None, False, (), None
            )
        assert excinfo.group_contains(LeaseError, match="member 'phone' lost its exporter")

    def test_lost_member_is_reported_once_and_the_session_goes_on(self, capsys):
        lease: Any = FakeLease()
        polls = 0

        async def get():
            nonlocal polls
            polls += 1
            return Lease.from_protobuf(members_lease_pb(statuses=[lost_status()]))

        lease.get = get

        async def main():
            scope = anyio.CancelScope()
            with patch("jumpstarter_cli.shell._SHARED_ACCESS_POLL_SECONDS", 0.01), scope:
                with anyio.move_on_after(0.2):
                    await _monitor_lost_members(lease, scope)

        anyio.run(main)
        out = capsys.readouterr().out
        assert polls > 1
        assert out.count("member 'headunit' lost its exporter") == 1
        assert "The other members stay usable" in out
        assert "headunit" in lease.lost


def test_lease_picker_describes_a_lease_with_members():
    lease = Lease.from_protobuf(members_lease_pb(template="ivi"))
    text = _format_lease_display(lease)
    assert "template=ivi" in text
    assert "members=phone=rack3-phone-4,headunit=rack3-hu-2" in text
