"""`j-forward status` (JEP-0016, DD-12)."""

import json
from unittest.mock import Mock, patch

import pytest
import yaml
from click.testing import CliRunner

from jumpstarter_cli.j_forward import ForwardStatusList, forward_status_list, j_forward

from jumpstarter.client._members_testing import exporter_pb, forward_pb, forward_status_pb, members_lease_pb
from jumpstarter.client.grpc import Exporter, Lease
from jumpstarter.common.exceptions import ConnectionError
from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter.config.env import JMP_CLIENT_CONFIG, JMP_LEASE
from jumpstarter.utils.env import JMP_LEASE_FORWARDS

EXPORTERS = {
    "rack3-hu-2": Exporter.from_protobuf(
        exporter_pb("rack3-hu-2", [("rootcanal", "provides", "hci-h4"), ("uart", "connects"), ("console", "provides")])
    ),
    "rack3-phone-4": Exporter.from_protobuf(
        exporter_pb("rack3-phone-4", [("controller", "requires"), ("uart", "connects"), ("console", "requires")])
    ),
}
SNAPSHOT = [
    {
        "name": "bt",
        "endpoints": [
            {"member": "headunit", "port": "rootcanal", "direction": "provides"},
            {"member": "phone", "port": "controller", "direction": "requires"},
        ],
        "state": "Pending",
        "message": None,
    }
]
FAILED_ENDS = [("headunit", "Ready"), ("phone", "Failed", "address already in use")]


def lease(*statuses, forwards=None) -> Lease:
    return Lease.from_protobuf(
        members_lease_pb(
            forwards=forwards if forwards is not None else [forward_pb()],
            forward_statuses=statuses or [forward_status_pb("bt", "Connected")],
        )
    )


def client_config(result) -> Mock:
    config = Mock(spec=ClientConfigV1Alpha1)
    if isinstance(result, Exception):
        config.get_lease.side_effect = result
    else:
        config.get_lease.return_value = result
    config.get_exporter.side_effect = lambda name: EXPORTERS[name]
    return config


def run(config, *args, env=None):
    """Run `j-forward status` with config as the client config of the user."""
    with patch("jumpstarter_cli_common.config.ClientConfigV1Alpha1", side_effect=lambda: config), patch(
        "jumpstarter_cli.j_forward.ClientConfigV1Alpha1", type(config) if config is not None else ClientConfigV1Alpha1
    ):
        return CliRunner(env={"COLUMNS": "200", JMP_LEASE: "lease-9m2qd", **(env or {})}).invoke(
            j_forward, ["status", *args]
        )


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    monkeypatch.delenv(JMP_LEASE, raising=False)
    monkeypatch.delenv(JMP_LEASE_FORWARDS, raising=False)
    monkeypatch.delenv(JMP_CLIENT_CONFIG, raising=False)


class TestStatusList:
    def test_states_come_from_the_lease_and_directions_from_the_exporters(self):
        config = client_config(lease())
        forwards = forward_status_list(config, "lease-9m2qd", in_shell=False)
        assert forwards.lease == "lease-9m2qd"
        (bt,) = forwards.forwards
        assert (bt.name, bt.state, bt.message) == ("bt", "Connected", None)
        assert [(e.member, e.port, e.direction) for e in bt.endpoints] == [
            ("headunit", "rootcanal", "provides"),
            ("phone", "controller", "requires"),
        ]
        assert bt.describe() == "headunit.rootcanal → phone.controller"
        config.get_lease.assert_called_once_with(name="lease-9m2qd")

    def test_shell_directions_save_the_exporter_reads(self, monkeypatch):
        monkeypatch.setenv(JMP_LEASE_FORWARDS, json.dumps(SNAPSHOT))
        config = client_config(lease(forward_status_pb("bt", "Listening")))
        forwards = forward_status_list(config, "lease-9m2qd", in_shell=True)
        # the state is the one of the lease now, not the one at the start of the shell
        assert forwards.forwards[0].state == "Listening"
        assert forwards.forwards[0].describe() == "headunit.rootcanal → phone.controller"
        config.get_exporter.assert_not_called()

    def test_path_and_many_requires_ports(self):
        config = client_config(
            lease(
                forward_status_pb("uart", "Listening"),
                forwards=[
                    forward_pb("uart", "headunit.uart", "phone.uart"),
                    forward_pb("console", "phone.console", "headunit.console"),
                ],
            )
        )
        uart, console = forward_status_list(config, "lease-9m2qd", in_shell=False).forwards
        assert uart.describe() == "headunit.uart ↔ phone.uart"
        # the endpoints can be in any sequence: the arrow starts at the provides port
        assert console.describe() == "headunit.console → phone.console"
        assert console.state == "Pending"  # no status yet

    def test_ends_come_from_the_lease_status(self):
        config = client_config(lease(forward_status_pb("bt", "Failed", "The setup failed", ends=FAILED_ENDS)))
        (bt,) = forward_status_list(config, "lease-9m2qd", in_shell=False).forwards
        assert [(e.member, e.state, e.message) for e in bt.ends] == [
            ("headunit", "Ready", None),
            ("phone", "Failed", "address already in use"),
        ]
        assert bt.describe_ends() == "headunit: Ready\nphone: Failed (address already in use)"

    def test_port_that_no_exporter_reports_has_no_arrow(self):
        config = client_config(lease(forwards=[forward_pb("can", "headunit.can", "phone.can-remote")]))
        (can,) = forward_status_list(config, "lease-9m2qd", in_shell=False).forwards
        assert can.describe() == "headunit.can, phone.can-remote"
        assert [e.direction for e in can.endpoints] == [None, None]


class TestCommand:
    def test_table_as_in_the_jep(self):
        result = run(client_config(lease()))
        assert result.exit_code == 0, result.output
        lines = [line.rstrip() for line in result.output.splitlines() if line.strip()]
        assert lines[0].split() == ["NAME", "ENDPOINTS", "STATE"]
        assert lines[1].split() == ["bt", "headunit.rootcanal", "→", "phone.controller", "Connected"]

    def test_failed_and_disabled_forwards_show_the_cause(self):
        result = run(
            client_config(
                lease(
                    forward_status_pb("bt", "Failed", "the listen address is in use"),
                    forward_status_pb("can", "Disabled", "member 'phone' is omitted"),
                    forwards=[forward_pb(), forward_pb("can", "headunit.can", "phone.can-remote")],
                )
            )
        )
        assert result.exit_code == 0
        assert "MESSAGE" in result.output
        assert "Failed" in result.output and "the listen address is in use" in result.output
        assert "Disabled" in result.output and "member 'phone' is omitted" in result.output

    def test_forward_that_is_not_usable_shows_the_state_and_the_message_of_each_end(self):
        result = run(
            client_config(
                lease(
                    forward_status_pb("bt", "Failed", "The setup failed", ends=FAILED_ENDS),
                    forward_status_pb("can", "Pending", ends=[("headunit", "Ready"), ("phone", "Pending")]),
                    forward_status_pb("uart", "Connected", ends=[("headunit", "Connected"), ("phone", "Ready")]),
                    forwards=[
                        forward_pb(),
                        forward_pb("can", "headunit.can", "phone.can-remote"),
                        forward_pb("uart", "headunit.uart", "phone.uart"),
                    ],
                )
            )
        )
        assert result.exit_code == 0, result.output
        lines = [line.rstrip() for line in result.output.splitlines() if line.strip()]
        assert lines[0].split() == ["NAME", "ENDPOINTS", "STATE", "MESSAGE", "ENDS"]
        assert "headunit: Ready" in lines[1] and lines[1].split()[0] == "bt"
        assert "phone: Failed (address already in use)" in lines[2]
        assert "headunit: Ready" in lines[3] and lines[3].split()[0] == "can"
        assert "phone: Pending" in lines[4]
        # a forward that is Listening or Connected does not show its ends
        assert lines[5].split() == ["uart", "headunit.uart", "↔", "phone.uart", "Connected"]
        assert len(lines) == 6

    def test_usable_forwards_have_no_ends_column(self):
        result = run(
            client_config(lease(forward_status_pb("bt", "Listening", ends=[("headunit", "Ready"), ("phone", "Ready")])))
        )
        assert result.exit_code == 0 and "ENDS" not in result.output and "headunit: Ready" not in result.output

    @pytest.mark.parametrize("output", ["json", "yaml"])
    def test_structured_output_has_the_ends(self, output):
        result = run(
            client_config(
                lease(forward_status_pb("bt", "Connected", ends=[("headunit", "Connected"), ("phone", "Ready")]))
            ),
            "-o",
            output,
        )
        data = json.loads(result.output) if output == "json" else yaml.safe_load(result.output)
        assert data["forwards"][0]["state"] == "Connected"
        assert data["forwards"][0]["ends"] == [
            {"member": "headunit", "state": "Connected", "message": None},
            {"member": "phone", "state": "Ready", "message": None},
        ]

    @pytest.mark.parametrize("output", ["json", "yaml"])
    def test_structured_output(self, output):
        result = run(client_config(lease(forward_status_pb("bt", "Failed", "no report in 60s"))), "-o", output)
        data = json.loads(result.output) if output == "json" else yaml.safe_load(result.output)
        assert data == {
            "lease": "lease-9m2qd",
            "forwards": [
                {
                    "name": "bt",
                    "endpoints": [
                        {"member": "headunit", "port": "rootcanal", "direction": "provides"},
                        {"member": "phone", "port": "controller", "direction": "requires"},
                    ],
                    "state": "Failed",
                    "message": "no report in 60s",
                }
            ],
        }

    def test_name_output(self):
        assert run(client_config(lease()), "-o", "name").output.split() == ["bt"]

    def test_lease_without_forwards(self):
        result = run(client_config(Lease.from_protobuf(members_lease_pb())))
        assert result.exit_code == 0 and result.output.strip() == "Lease lease-9m2qd has no forwards."
        result = run(client_config(Lease.from_protobuf(members_lease_pb())), "-o", "json")
        assert json.loads(result.output) == {"lease": "lease-9m2qd", "forwards": []}

    def test_outside_a_shell_it_needs_a_lease(self):
        result = run(client_config(lease()), env={JMP_LEASE: ""})
        assert result.exit_code == 2
        assert "j-forward must be used inside a jmp shell, or with --lease NAME" in result.output

    def test_lease_option_names_another_lease(self):
        config = client_config(lease())
        result = run(config, "--lease", "other", env={JMP_LEASE: "", JMP_LEASE_FORWARDS: json.dumps(SNAPSHOT)})
        assert result.exit_code == 0
        config.get_lease.assert_called_once_with(name="other")

    def test_lease_that_cannot_be_read_shows_the_states_of_the_shell_start(self):
        result = run(client_config(RuntimeError("controller is down")), env={JMP_LEASE_FORWARDS: json.dumps(SNAPSHOT)})
        assert result.exit_code == 0
        assert "cannot read lease lease-9m2qd (controller is down)" in result.output
        assert "These are the states at the start of the shell" in result.output
        assert "headunit.rootcanal → phone.controller" in result.output and "Pending" in result.output

    def test_lease_that_cannot_be_read_shows_the_ends_of_the_shell_start(self):
        snapshot = [
            {**SNAPSHOT[0], "ends": [{"member": "headunit", "state": "Ready", "message": None}, {"member": "phone"}]}
        ]
        result = run(client_config(RuntimeError("controller is down")), env={JMP_LEASE_FORWARDS: json.dumps(snapshot)})
        assert result.exit_code == 0
        assert "ENDS" in result.output and "headunit: Ready" in result.output and "phone: Pending" in result.output

    def test_lease_that_cannot_be_read_without_a_record_is_an_error(self):
        result = run(client_config(ConnectionError("controller is down")))
        assert result.exit_code != 0 and "controller is down" in result.output

    def test_no_client_config(self):
        # opt_config gives a placeholder when the user has no client config
        with patch("jumpstarter_cli_common.config.ClientConfigV1Alpha1", side_effect=lambda: None), patch(
            "jumpstarter_cli_common.config.UserConfigV1Alpha1.load_or_create",
            return_value=Mock(config=Mock(current_client=None)),
        ):
            runner = CliRunner(env={JMP_LEASE: "lease-9m2qd"})
            result = runner.invoke(j_forward, ["status"])
            assert result.exit_code != 0 and "no client config is set" in result.output
            runner = CliRunner(env={JMP_LEASE: "lease-9m2qd", JMP_LEASE_FORWARDS: json.dumps(SNAPSHOT)})
            result = runner.invoke(j_forward, ["status"])
            assert result.exit_code == 0 and "no client config is set" in result.output and "bt" in result.output

    def test_client_config_of_the_shell_reads_the_lease(self, tmp_path):
        """In `jmp shell --client-config PATH`, the lease is read with that client, not with the default one."""
        path = tmp_path / "lab.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "jumpstarter.dev/v1alpha1",
                    "kind": "ClientConfig",
                    "metadata": {"namespace": "jumpstarter-lab", "name": "lab"},
                    "endpoint": "grpc.lab.example.com:443",
                    "token": "token-of-lab",
                }
            )
        )
        used = []

        def get_lease(self, name):
            used.append((self.metadata.name, self.path, name))
            return lease(forward_status_pb("bt", "Listening"))

        default = client_config(RuntimeError("Failed to connect to 127.0.0.1:8082"))
        with patch.object(ClientConfigV1Alpha1, "get_lease", get_lease), patch(
            "jumpstarter_cli_common.config.UserConfigV1Alpha1.load_or_create",
            return_value=Mock(config=Mock(current_client=default)),
        ):
            runner = CliRunner(
                env={
                    "COLUMNS": "200",
                    JMP_LEASE: "lease-9m2qd",
                    JMP_LEASE_FORWARDS: json.dumps(SNAPSHOT),
                    JMP_CLIENT_CONFIG: str(path),
                }
            )
            result = runner.invoke(j_forward, ["status"])
        assert result.exit_code == 0, result.output
        assert used == [("lab", path, "lease-9m2qd")]
        default.get_lease.assert_not_called()
        # the state of the lease now, and not the one at the start of the shell
        assert "Listening" in result.output and "cannot read lease" not in result.output

    def test_client_config_of_the_shell_that_is_missing_is_an_error(self, tmp_path):
        runner = CliRunner(env={JMP_LEASE: "lease-9m2qd", JMP_CLIENT_CONFIG: str(tmp_path / "missing.yaml")})
        result = runner.invoke(j_forward, ["status"])
        assert result.exit_code != 0 and "Failed to load config" in result.output

    def test_help_and_group(self):
        runner = CliRunner()
        assert "status" in runner.invoke(j_forward, ["--help"]).output
        help_text = runner.invoke(j_forward, ["status", "--help"]).output
        for state in ("Pending", "Listening", "Connected", "Disabled", "Failed"):
            assert state in help_text

    def test_model_prints_the_message_column_only_with_a_cause(self):
        from rich.table import Table

        forwards = ForwardStatusList.model_validate(
            {"lease": "l", "forwards": [{"name": "bt", "endpoints": [], "state": "Listening"}]}
        )
        table = Table()
        forwards.rich_add_columns(table)
        assert [column.header for column in table.columns] == ["NAME", "ENDPOINTS", "STATE"]


def test_console_script_is_declared():
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())
    assert pyproject["project"]["scripts"]["j-forward"] == "jumpstarter_cli.j_forward:j_forward"
