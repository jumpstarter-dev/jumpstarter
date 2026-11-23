import json
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from unittest.mock import Mock

import anyio
import click
from click.testing import CliRunner

from jumpstarter_cli.j import RolesGroup, _in_members_shell, _members_cli, roles_cli

from jumpstarter.client import DriverClient
from jumpstarter.common import ExporterStatus
from jumpstarter.config.env import JMP_DRIVERS_ALLOW, JMP_LEASE, JMP_LEASE_MEMBERS, JUMPSTARTER_HOST
from jumpstarter.driver import Driver, export
from jumpstarter.exporter import Session

MEMBERS = {
    "phone": {"exporter": "rack3-phone-4", "labels": {}, "socket": "/x/phone.sock"},
    "headunit": {"exporter": "rack3-hu-2", "labels": {}, "socket": "/x/headunit.sock"},
    "tablet": None,
}


def role_command(role):
    @click.group()
    def base():
        """Generic composite device"""

    @base.command()
    def whoami():
        click.echo(f"driver of {role}")

    return Mock(cli=Mock(return_value=base))


def test_j_alone_lists_the_roles_without_a_connection(monkeypatch):
    monkeypatch.setenv(JMP_LEASE, "lease-9m2qd")
    connect = Mock()
    result = CliRunner().invoke(roles_cli(MEMBERS, connect), [])
    assert "The members of the lease lease-9m2qd" in result.output
    assert "Roles:" in result.output
    assert "phone     exporter rack3-phone-4" in result.output
    assert "tablet    (no exporter)" in result.output
    connect.assert_not_called()


def test_role_is_the_top_level_and_only_that_role_is_connected():
    connect = Mock(side_effect=role_command)
    result = CliRunner().invoke(roles_cli(MEMBERS, connect), ["headunit", "whoami"])
    assert result.exit_code == 0
    assert result.output == "driver of headunit\n"
    connect.assert_called_once_with("headunit")


def test_role_without_exporter_says_so():
    connect = Mock()
    result = CliRunner().invoke(roles_cli(MEMBERS, connect), ["tablet", "whoami"])
    assert result.exit_code != 0
    assert "the role 'tablet' has no exporter in this lease" in result.output
    connect.assert_not_called()


def test_unknown_role():
    result = CliRunner().invoke(roles_cli(MEMBERS, Mock()), ["ecu", "whoami"])
    assert result.exit_code == 2
    assert "No such command 'ecu'" in result.output


def test_completion_lists_roles_and_drivers():
    from click.shell_completion import ShellComplete

    connect = Mock(side_effect=role_command)
    group = roles_cli(MEMBERS, connect)
    complete = ShellComplete(group, {}, "j", "_J_COMPLETE")
    assert [c.value for c in complete.get_completions([], "")] == ["phone", "headunit"]
    connect.assert_not_called()
    assert [c.value for c in complete.get_completions(["phone"], "")] == ["whoami"]


def test_members_shell_detection(monkeypatch):
    monkeypatch.delenv(JUMPSTARTER_HOST, raising=False)
    monkeypatch.delenv(JMP_LEASE_MEMBERS, raising=False)
    assert not _in_members_shell()
    monkeypatch.setenv(JMP_LEASE_MEMBERS, json.dumps(MEMBERS))
    assert _in_members_shell()
    # a shell on one exporter, started in a members shell, wins
    monkeypatch.setenv(JUMPSTARTER_HOST, "/x/socket")
    assert not _in_members_shell()
    assert isinstance(roles_cli(MEMBERS, Mock()), RolesGroup)


class EchoClient(DriverClient):
    def cli(self):
        @click.group()
        def base():
            """Echo device"""

        @base.command()
        def whoami():
            click.echo(self.call("whoami"))

        return base


@dataclass(kw_only=True)
class EchoDriver(Driver):
    name: str = ""

    @classmethod
    def client(cls) -> str:
        return f"{EchoClient.__module__}.EchoClient"

    @export
    def whoami(self) -> str:
        return f"driver of {self.name}"


@contextmanager
def members_shell(monkeypatch):
    with ExitStack() as stack:
        members: dict[str, dict | None] = {}
        for role in ("phone", "headunit"):
            session = stack.enter_context(Session(root_device=EchoDriver(name=role)))
            path = stack.enter_context(session.serve_unix())
            session.update_status(ExporterStatus.LEASE_READY)
            members[role] = {"exporter": f"rack3-{role}", "labels": {}, "socket": str(path)}
        members["tablet"] = None
        monkeypatch.delenv(JUMPSTARTER_HOST, raising=False)
        monkeypatch.setenv(JMP_LEASE_MEMBERS, json.dumps(members))
        monkeypatch.setenv(JMP_DRIVERS_ALLOW, "UNSAFE")
        yield


def test_j_role_driver_runs_against_the_exporter_of_the_role(monkeypatch, capsys):
    with members_shell(monkeypatch):
        monkeypatch.setattr(sys, "argv", ["j", "headunit", "--help"])
        anyio.run(_members_cli)
        out = capsys.readouterr().out
        assert "Usage: j headunit" in out and "whoami" in out

        for role in ("headunit", "phone"):
            monkeypatch.setattr(sys, "argv", ["j", role, "whoami"])
            anyio.run(_members_cli)
            assert capsys.readouterr().out == f"driver of {role}\n"
