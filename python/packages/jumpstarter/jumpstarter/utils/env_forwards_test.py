"""The forwards of the lease of a `jmp shell`, as `env_members()` gives them (JEP-0016)."""

import json
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jumpstarter.client._members_testing import forward_pb, forward_status_pb, members_lease_pb
from jumpstarter.client.exceptions import ForwardError
from jumpstarter.client.grpc import Lease
from jumpstarter.client.lease import Forward, LeaseMembers
from jumpstarter.common.exceptions import EnvironmentVariableNotSetError
from jumpstarter.config.env import JMP_CLIENT_CONFIG
from jumpstarter.utils.env import JMP_LEASE_FORWARDS, _current_client_config, env_members, lease_forwards_from_env
from jumpstarter.utils.env_members_test import Headunit, members_shell

FORWARDS = [
    {
        "name": "bt",
        "endpoints": [
            {"member": "headunit", "port": "rootcanal", "direction": "provides"},
            {"member": "phone", "port": "controller", "direction": "requires"},
        ],
        "state": "Listening",
        "message": None,
    },
    {
        "name": "can",
        "endpoints": [{"member": "headunit", "port": "can"}, {"member": "phone", "port": "can-remote"}],
        "state": "Failed",
        "message": "port 'can' is not on exporter rack3-headunit",
    },
]


class Bench(LeaseMembers):
    phone: Headunit | None
    headunit: Headunit
    bt: Forward


@pytest.fixture
def no_controller():
    """A shell whose client config cannot be found: the forwards keep the states of the environment."""
    with patch("jumpstarter.utils.env._current_client_config", return_value=None):
        yield


def test_environment_without_forwards(monkeypatch):
    monkeypatch.delenv(JMP_LEASE_FORWARDS, raising=False)
    assert lease_forwards_from_env() == []
    with members_shell(monkeypatch), env_members() as members:
        assert dict(members.forwards) == {}


@pytest.mark.parametrize("value", ["{not json", '{"bt": {}}', '[{"endpoints": []}]', '["bt"]'])
def test_invalid_environment(monkeypatch, value):
    monkeypatch.setenv(JMP_LEASE_FORWARDS, value)
    with pytest.raises(EnvironmentVariableNotSetError, match="JMP_LEASE_FORWARDS is not"):
        lease_forwards_from_env()


def test_generic_members_have_the_forwards_of_the_shell(monkeypatch, no_controller):
    monkeypatch.setenv(JMP_LEASE_FORWARDS, json.dumps(FORWARDS))
    assert lease_forwards_from_env() == FORWARDS
    with members_shell(monkeypatch), env_members() as members:
        assert set(members.forwards) == {"bt", "can"}
        bt = members.forwards["bt"]
        assert isinstance(bt, Forward) and bt.state == "Listening"
        assert [str(endpoint) for endpoint in bt.endpoints] == ["headunit.rootcanal", "phone.controller"]
        assert bt.wait_listening(timeout=1) is bt
        can = members.forwards["can"]
        assert can.state == "Failed" and can.message == "port 'can' is not on exporter rack3-headunit"
        with pytest.raises(ForwardError, match="forward 'can' of lease lease-9m2qd failed: port 'can' is not"):
            can.wait_listening(timeout=1)
        headunit: Any = members.by_role["headunit"]
        headunit.power.on()


def test_members_class_gets_its_forward(monkeypatch, no_controller):
    monkeypatch.setenv(JMP_LEASE_FORWARDS, json.dumps(FORWARDS))
    with members_shell(monkeypatch), env_members(Bench) as members:
        assert isinstance(members.bt, Forward) and members.bt is members.forwards["bt"]
        assert members.bt.state == "Listening"
        members.headunit.power.on()


def test_class_forward_that_the_lease_of_the_shell_does_not_contain(monkeypatch):
    monkeypatch.setenv(JMP_LEASE_FORWARDS, json.dumps(FORWARDS[1:]))
    with members_shell(monkeypatch), pytest.raises(
        TypeError, match="Bench declares the forward 'bt', but the lease of this shell has no such forward"
    ), env_members(Bench):
        pass


CLIENT_YAML = """\
apiVersion: jumpstarter.dev/v1alpha1
kind: ClientConfig
metadata:
  namespace: jumpstarter-lab
  name: {name}
endpoint: grpc.lab.example.com:443
token: token-of-{name}
"""


def user_default(client):
    """The current client of the user config is `client`."""
    return patch(
        "jumpstarter.config.user.UserConfigV1Alpha1.load", return_value=Mock(config=Mock(current_client=client))
    )


def test_client_config_of_the_shell_is_the_file_that_the_shell_names(monkeypatch, tmp_path):
    """`jmp shell --client-config PATH` gives the path to its children: they do not use the default client."""
    path = tmp_path / "lab.yaml"
    path.write_text(CLIENT_YAML.format(name="lab"))
    monkeypatch.setenv(JMP_CLIENT_CONFIG, str(path))
    default = Mock(name="default client")
    with user_default(default):
        config = _current_client_config()
    assert config is not default
    assert (config.metadata.name, config.endpoint, config.path) == ("lab", "grpc.lab.example.com:443", path)


def test_client_config_file_that_cannot_be_read_is_no_client_config(monkeypatch, tmp_path):
    monkeypatch.setenv(JMP_CLIENT_CONFIG, str(tmp_path / "missing.yaml"))
    default = Mock(name="default client")
    with user_default(default):
        # the default client is a different client: it cannot read the lease of this shell
        assert _current_client_config() is None


def test_client_config_without_the_variable_is_the_current_client(monkeypatch):
    monkeypatch.delenv(JMP_CLIENT_CONFIG, raising=False)
    for name in ("JMP_ENDPOINT", "JMP_TOKEN", "JMP_NAME", "JMP_NAMESPACE"):
        monkeypatch.delenv(name, raising=False)
    default = Mock(name="default client")
    with user_default(default):
        assert _current_client_config() is default


def test_state_comes_from_the_lease_through_the_client_config(monkeypatch):
    monkeypatch.setenv(JMP_LEASE_FORWARDS, json.dumps(FORWARDS))
    lease = Lease.from_protobuf(
        members_lease_pb(
            forwards=[forward_pb(), forward_pb("can", "headunit.can", "phone.can-remote")],
            forward_statuses=[forward_status_pb("bt", "Connected"), forward_status_pb("can", "Listening")],
        )
    )
    config = Mock(get_lease=AsyncMock(return_value=lease))
    with (
        patch("jumpstarter.utils.env._current_client_config", return_value=config),
        patch("jumpstarter.client.lease._FORWARD_STATE_MAX_AGE", 0),
        members_shell(monkeypatch),
        env_members() as members,
    ):
        assert members.forwards["bt"].state == "Connected"
        # the forward that had failed when the shell started operates now
        assert members.forwards["can"].wait_listening(timeout=1).state == "Listening"
    config.get_lease.assert_awaited_with(name="lease-9m2qd")


def test_ends_come_from_the_environment_and_then_from_the_lease(monkeypatch):
    ends = [{"member": "headunit", "state": "Ready"}, {"member": "phone"}]
    started = [{**FORWARDS[0], "state": "Pending", "ends": ends}]
    monkeypatch.setenv(JMP_LEASE_FORWARDS, json.dumps(started))
    lease = Lease.from_protobuf(
        members_lease_pb(
            forwards=[forward_pb()],
            forward_statuses=[
                forward_status_pb(
                    "bt", "Failed", "The setup failed", [("headunit", "Ready"), ("phone", "Failed", "address in use")]
                )
            ],
        )
    )
    config = Mock(get_lease=AsyncMock(return_value=lease))
    with (
        patch("jumpstarter.utils.env._current_client_config", return_value=config),
        members_shell(monkeypatch),
        env_members() as members,
    ):
        bt = members.forwards["bt"]
        # the states at the start of the shell: an end without a state is Pending
        assert [(e.member_name, e.state, e.message) for e in bt.ends] == [
            ("headunit", "Ready", None),
            ("phone", "Pending", None),
        ]
        bt.refresh()
        assert [(e.member_name, e.state, e.message) for e in bt.ends] == [
            ("headunit", "Ready", None),
            ("phone", "Failed", "address in use"),
        ]


def test_pending_forward_without_a_client_config_times_out_with_the_reason(monkeypatch, no_controller):
    pending = [{**FORWARDS[0], "state": "Pending"}]
    monkeypatch.setenv(JMP_LEASE_FORWARDS, json.dumps(pending))
    with (
        patch("jumpstarter.client.lease._FORWARD_POLL_SECONDS", 0.01),
        members_shell(monkeypatch),
        env_members() as members,
    ):
        assert members.forwards["bt"].state == "Pending"
        with pytest.raises(TimeoutError, match="still Pending.*no client config is set"):
            members.forwards["bt"].wait_listening(timeout=0.05)
