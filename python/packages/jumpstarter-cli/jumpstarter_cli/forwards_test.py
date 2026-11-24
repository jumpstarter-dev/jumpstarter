"""`--forward`, ports and the output of forwards in `jmp` (JEP-0016)."""

import inspect
import json
from datetime import timedelta
from typing import Any
from unittest.mock import Mock, patch

import click
import pytest
import yaml
from click.testing import CliRunner

from jumpstarter_cli.create import create_lease
from jumpstarter_cli.describe import describe_exporter, describe_lease, describe_lease_template, forward_directions
from jumpstarter_cli.get import get_exporters, get_lease_templates, get_leases
from jumpstarter_cli.jmp import jmp
from jumpstarter_cli.members import MemberRequest, parse_member_request

from jumpstarter.client._members_testing import (
    exporter_pb,
    forward_pb,
    forward_status_pb,
    members_lease_pb,
    template_pb,
)
from jumpstarter.client.exceptions import ControllerTooOldError
from jumpstarter.client.grpc import (
    Exporter,
    ExporterList,
    ForwardEndpoint,
    Lease,
    LeaseForward,
    LeaseList,
    LeaseTemplate,
    LeaseTemplateList,
)

MEMBERS = ("peer=device-type=bt-peer", "headunit=device-type=aaos-headunit")
BT = LeaseForward(
    name="bt",
    endpoints=[
        ForwardEndpoint(member_name="headunit", port_name="rootcanal"),
        ForwardEndpoint(member_name="peer", port_name="controller"),
    ],
)
EXPORTERS = {
    "rack3-hu-2": Exporter.from_protobuf(exporter_pb("rack3-hu-2", [("rootcanal", "provides", "hci-h4")])),
    "rack3-phone-4": Exporter.from_protobuf(exporter_pb("rack3-phone-4", [("controller", "requires")])),
}


def parse(**kwargs):
    defaults: dict[str, Any] = {
        "lease_template": None,
        "without": (),
        "member_entries": (),
        "same_label_entries": (),
        "selector": None,
        "exporter_name": None,
    }
    return parse_member_request(**{**defaults, **kwargs})


ENDS = [("headunit", "Ready"), ("phone", "Failed", "address already in use")]


def forwards_lease(state="Connected", message=None, ends=(), **kwargs) -> Lease:
    return Lease.from_protobuf(
        members_lease_pb(
            forwards=[forward_pb(), forward_pb("can", "headunit.can", "phone.can-remote")],
            forward_statuses=[forward_status_pb("bt", state, message, ends)],
            **kwargs,
        )
    )


def lease_config(lease: Lease) -> Mock:
    config = Mock()
    config.get_lease.return_value = lease
    config.get_exporter.side_effect = lambda name: EXPORTERS[name]
    return config


class TestParseForward:
    def test_forward_of_ad_hoc_members(self):
        request = parse(member_entries=MEMBERS, forward_entries=("bt=headunit.rootcanal,peer.controller",))
        assert isinstance(request, MemberRequest) and request.forwards == [BT]
        kwargs = request.create_kwargs()
        assert kwargs["forwards"] == [BT] and [m.name for m in kwargs["members"]] == ["peer", "headunit"]

    def test_request_without_forward_sets_no_forwards(self):
        request = parse(member_entries=MEMBERS)
        assert request is not None and request.forwards == [] and "forwards" not in request.create_kwargs()

    def test_more_than_two_endpoints_and_more_than_one_forward(self):
        request = parse(
            member_entries=("dut=a=b", "logger=a=c", "debugger=a=d"),
            forward_entries=("console=dut.console, logger.console,debugger.console", "uart=dut.uart,logger.uart"),
        )
        assert request is not None
        assert [str(e) for e in request.forwards[0].endpoints] == ["dut.console", "logger.console", "debugger.console"]
        assert [f.name for f in request.forwards] == ["console", "uart"]

    @pytest.mark.parametrize(
        "kwargs,message",
        [
            # a forward connects members: the other request forms have none of their own
            ({"forward_entries": ("bt=a.x,b.y",)}, "--forward needs --member"),
            ({"forward_entries": ("bt=a.x,b.y",), "selector": "a=b"}, "--forward needs --member"),
            ({"forward_entries": ("bt=a.x,b.y",), "exporter_name": "exp"}, "--forward needs --member"),
            (
                {"lease_template": "ivi", "forward_entries": ("bt=headunit.rootcanal,phone.controller",)},
                "--forward cannot be used with --lease-template/-t: the template has the forwards",
            ),
            (
                {"lease_template": "ivi", "member_entries": ("headunit=a=b",), "forward_entries": ("bt=a.x,b.y",)},
                "--forward cannot be used with --lease-template/-t",
            ),
            # formats
            ({"member_entries": MEMBERS, "forward_entries": ("bt",)}, "Invalid --forward format: 'bt'"),
            ({"member_entries": MEMBERS, "forward_entries": ("bt=",)}, "Invalid --forward format"),
            ({"member_entries": MEMBERS, "forward_entries": ("=headunit.x,peer.y",)}, "Invalid --forward format"),
            ({"member_entries": MEMBERS, "forward_entries": ("BT=headunit.x,peer.y",)}, "invalid forward name 'BT'"),
            ({"member_entries": MEMBERS, "forward_entries": ("bt=headunit,peer.y",)}, "invalid endpoint 'headunit'"),
            ({"member_entries": MEMBERS, "forward_entries": ("bt=headunit.,peer.y",)}, "invalid endpoint 'headunit.'"),
            ({"member_entries": MEMBERS, "forward_entries": ("bt=headunit.x,,peer.y",)}, "invalid endpoint ''"),
            ({"member_entries": MEMBERS, "forward_entries": ("bt=headunit.X,peer.y",)}, "invalid port name 'X'"),
            (
                {"member_entries": MEMBERS, "forward_entries": ("bt=headunit.a.b,peer.y",)},
                "invalid port name 'a.b'",
            ),
            # endpoints
            (
                {"member_entries": MEMBERS, "forward_entries": ("bt=headunit.x,tablet.y",)},
                "no --member defines the role 'tablet'",
            ),
            (
                {"member_entries": MEMBERS, "forward_entries": ("bt=headunit.x",)},
                "a forward connects two or more endpoints",
            ),
            (
                {"member_entries": MEMBERS, "forward_entries": ("bt=headunit.x,headunit.y",)},
                "two endpoints on the member 'headunit'",
            ),
            (
                {
                    "member_entries": tuple(f"m{i}=a=b" for i in range(8)),
                    "forward_entries": ("bus=" + ",".join(f"m{i % 8}.p{i}" for i in range(9)),),
                },
                "too many endpoints \\(max 8\\)",
            ),
            # names
            (
                {"member_entries": MEMBERS, "forward_entries": ("bt=headunit.x,peer.y", "bt=headunit.z,peer.w")},
                "the forward 'bt' is given more than once",
            ),
            (
                {"member_entries": MEMBERS, "forward_entries": ("peer=headunit.x,peer.y",)},
                "'peer' is a member: a forward and a member cannot have the same name",
            ),
            (
                {
                    "member_entries": MEMBERS,
                    "forward_entries": tuple(f"f{i}=headunit.x{i},peer.y{i}" for i in range(17)),
                },
                "Too many --forward entries \\(max 16\\)",
            ),
        ],
    )
    def test_rejected_combinations(self, kwargs, message):
        with pytest.raises(click.UsageError, match=message):
            parse(**kwargs)


def create(config, **kwargs):
    defaults = {
        "config": config,
        "selector": None,
        "exporter_name": None,
        "duration": timedelta(minutes=45),
        "begin_time": None,
        "lease_id": None,
        "tags": (),
        "allow_disabled": False,
        "context_entries": (),
        "share_with": None,
        "output": None,
    }
    assert create_lease.callback is not None
    with patch("jumpstarter_cli.create.model_print") as model_print:
        inspect.unwrap(create_lease.callback)(**{**defaults, **kwargs})
    return model_print


class TestCreateLease:
    def _config(self):
        config = Mock()
        config.create_lease.return_value = Mock(deprecated_labels={})
        return config

    def test_forward_is_a_part_of_the_members_request(self):
        config = self._config()
        create(config, member_entries=MEMBERS, forward_entries=("bt=headunit.rootcanal,peer.controller",))
        kwargs = config.create_lease.call_args.kwargs
        assert kwargs["forwards"] == [BT]
        assert [m.name for m in kwargs["members"]] == ["peer", "headunit"]
        assert "template" not in kwargs and "selector" not in kwargs

    def test_request_without_forward_is_the_request_of_before(self):
        config = self._config()
        create(config, member_entries=MEMBERS)
        assert "forwards" not in config.create_lease.call_args.kwargs

    @pytest.mark.parametrize(
        "kwargs,message",
        [
            ({"selector": "a=b"}, "--forward needs --member"),
            ({"lease_template": "ivi"}, "--forward cannot be used with --lease-template/-t"),
            ({"member_entries": MEMBERS}, "no --member defines the role 'tablet'"),
        ],
    )
    def test_invalid_combination_makes_no_request(self, kwargs, message):
        config = self._config()
        with pytest.raises(click.UsageError, match=message):
            create(config, forward_entries=("bt=headunit.rootcanal,tablet.controller",), **kwargs)
        config.create_lease.assert_not_called()
        config.get_lease_template.assert_not_called()

    def test_controller_without_forwards_is_reported(self):
        config = self._config()
        config.create_lease.side_effect = ControllerTooOldError("the controller is too old for port forwards")
        with pytest.raises(ControllerTooOldError, match="too old for port forwards"):
            create(config, member_entries=MEMBERS, forward_entries=("bt=headunit.rootcanal,peer.controller",))

    def test_command_line(self):
        runner = CliRunner()
        result = runner.invoke(
            jmp, ["create", "lease", "-l", "a=b", "--forward", "bt=a.x,b.y", "--duration", "1h"]
        )
        assert result.exit_code == 2 and "--forward needs --member" in result.output
        result = runner.invoke(
            jmp, ["create", "lease", "-t", "ivi", "--forward", "bt=headunit.x,phone.y", "--duration", "1h"]
        )
        assert result.exit_code == 2 and "--forward cannot be used with --lease-template/-t" in result.output
        help_text = runner.invoke(jmp, ["create", "lease", "--help"]).output
        assert "--forward NAME=ROLE.PORT,ROLE.PORT" in help_text
        assert "--forward NAME=ROLE.PORT,ROLE.PORT" in runner.invoke(jmp, ["shell", "--help"]).output


class TestGetExporters:
    def _get(self, config, with_options, output=None):
        assert get_exporters.callback is not None
        inspect.unwrap(get_exporters.callback)(
            config=config,
            selector="device-type=aaos-headunit",
            output=output,
            with_options=with_options,
            allow_disabled=False,
            show_hidden_labels=False,
            page_size=100,
        )

    def _exporters(self, include_ports=True):
        exporters = ExporterList(
            exporters=[EXPORTERS["rack3-hu-2"], Exporter.from_protobuf(exporter_pb("old-exporter"))],
            next_page_token=None,
        )
        exporters.include_ports = include_ports
        return exporters

    def test_with_ports_asks_for_the_ports(self):
        config = Mock()
        config.list_exporters.return_value = self._exporters()
        with patch("jumpstarter_cli.get.model_print"):
            self._get(config, ["ports"])
        assert config.list_exporters.call_args.kwargs["include_ports"] is True

    def test_without_it_the_request_is_the_one_of_before(self):
        config = Mock()
        with patch("jumpstarter_cli.get.model_print"):
            self._get(config, ["online"])
        assert "include_ports" not in config.list_exporters.call_args.kwargs

    def test_table(self, capsys):
        config = Mock()
        config.list_exporters.return_value = self._exporters()
        self._get(config, ["ports"])
        out = capsys.readouterr().out
        assert "PORTS" in out and "rootcanal (provides, hci-h4)" in out and "old-exporter" in out

    def test_json_as_in_the_jep(self, capsys):
        config = Mock()
        config.list_exporters.return_value = self._exporters()
        self._get(config, ["ports"], output="json")
        data = json.loads(capsys.readouterr().out)
        assert data["exporters"][0]["ports"] == [{"name": "rootcanal", "direction": "provides", "protocol": "hci-h4"}]
        assert data["exporters"][1]["ports"] == []

    def test_yaml(self, capsys):
        config = Mock()
        config.list_exporters.return_value = self._exporters()
        self._get(config, ["ports"], output="yaml")
        assert yaml.safe_load(capsys.readouterr().out)["exporters"][0]["ports"][0]["direction"] == "provides"

    def test_command_line_accepts_ports(self):
        result = CliRunner().invoke(jmp, ["get", "exporters", "--with", "nothing"])
        assert result.exit_code == 2 and "ports" in result.output
        assert "ports" in CliRunner().invoke(jmp, ["get", "exporters", "--help"]).output


class TestGetLeases:
    def _get(self, config, **kwargs):
        defaults = {
            "config": config,
            "selector": None,
            "output": None,
            "show_all": False,
            "all_clients": True,
            "tag_filter": None,
            "page_size": 100,
            "name": None,
        }
        assert get_leases.callback is not None
        inspect.unwrap(get_leases.callback)(**{**defaults, **kwargs})

    def test_lease_shows_the_state_of_each_forward(self, capsys):
        self._get(lease_config(forwards_lease("Listening")), name="lease-9m2qd")
        out = capsys.readouterr().out
        assert "FORWARDS" in out and "bt=Listening," in out and "can=Pending" in out

    def test_list_has_the_column_only_with_forwards(self, capsys):
        config = Mock()
        config.list_leases.return_value = LeaseList(leases=[forwards_lease("Failed")], next_page_token=None)
        self._get(config)
        assert "bt=Failed" in capsys.readouterr().out
        config.list_leases.return_value = LeaseList(
            leases=[Lease.from_protobuf(members_lease_pb())], next_page_token=None
        )
        self._get(config)
        assert "FORWARDS" not in capsys.readouterr().out

    @pytest.mark.parametrize("output", ["json", "yaml"])
    def test_structured_output(self, output, capsys):
        self._get(lease_config(forwards_lease("Failed", "no report in 60s")), name="lease-9m2qd", output=output)
        out = capsys.readouterr().out
        data = json.loads(out) if output == "json" else yaml.safe_load(out)
        assert data["forwards"][0]["name"] == "bt"
        assert data["forwards"][0]["endpoints"][0] == {"member_name": "headunit", "port_name": "rootcanal"}
        assert data["forward_statuses"] == [{"name": "bt", "state": "Failed", "message": "no report in 60s"}]

    @pytest.mark.parametrize("output", ["json", "yaml"])
    def test_structured_output_has_the_ends_of_each_forward(self, output, capsys):
        self._get(lease_config(forwards_lease("Pending", ends=ENDS)), name="lease-9m2qd", output=output)
        out = capsys.readouterr().out
        data = json.loads(out) if output == "json" else yaml.safe_load(out)
        assert data["forward_statuses"][0]["ends"] == [
            {"member_name": "headunit", "state": "Ready", "message": None},
            {"member_name": "phone", "state": "Failed", "message": "address already in use"},
        ]

    def test_lease_without_forwards_is_unchanged(self, capsys):
        self._get(lease_config(Lease.from_protobuf(members_lease_pb())), name="lease-9m2qd", output="json")
        data = json.loads(capsys.readouterr().out)
        assert "forwards" not in data and "forward_statuses" not in data


class TestDescribe:
    def _describe_lease(self, config, output=None):
        assert describe_lease.callback is not None
        inspect.unwrap(describe_lease.callback)(config=config, name="lease-9m2qd", show_drivers=False, output=output)

    def test_lease_has_a_table_of_its_forwards(self, capsys):
        self._describe_lease(lease_config(forwards_lease("Failed", "port 'rootcanal' is not on exporter rack3-hu-2")))
        out = capsys.readouterr().out
        assert "Forwards:" in out
        for header in ("Name", "Endpoints", "State", "Message"):
            assert header in out
        # the arrow points from the provides port to the requires port
        assert "headunit.rootcanal → phone.controller" in out
        assert "Failed" in out and "port 'rootcanal' is not on exporter rack3-hu-2" in out
        # the exporters do not report the ports of `can`: no arrow
        assert "headunit.can, phone.can-remote" in out and "Pending" in out

    def test_lease_shows_the_ends_of_a_forward_that_is_not_usable(self, capsys):
        self._describe_lease(lease_config(forwards_lease("Failed", "The setup failed", ends=ENDS)))
        out = capsys.readouterr().out
        header = next(line for line in out.splitlines() if line.split()[:1] == ["Name"] and "Endpoints" in line)
        assert header.split() == ["Name", "Endpoints", "State", "Message", "Ends"]
        assert "headunit: Ready; phone: Failed (address already in use)" in out

    @pytest.mark.parametrize("state", ["Listening", "Connected"])
    def test_lease_does_not_show_the_ends_of_a_usable_forward(self, state, capsys):
        self._describe_lease(lease_config(forwards_lease(state, ends=[("headunit", "Ready"), ("phone", "Ready")])))
        out = capsys.readouterr().out
        assert "Ends" not in out and "headunit: Ready" not in out

    def test_lease_json_has_the_ends(self, capsys):
        self._describe_lease(lease_config(forwards_lease("Failed", "The setup failed", ends=ENDS)), output="json")
        data = json.loads(capsys.readouterr().out)
        assert data["forward_statuses"][0]["ends"][1] == {
            "member_name": "phone",
            "state": "Failed",
            "message": "address already in use",
        }

    def test_directions_come_from_the_exporters_of_the_bound_members(self):
        lease = forwards_lease()
        config = lease_config(lease)
        assert forward_directions(config, lease) == {
            ("headunit", "rootcanal"): "provides",
            ("phone", "controller"): "requires",
        }
        # an exporter that cannot be read leaves its ports out
        config.get_exporter.side_effect = RuntimeError("down")
        assert forward_directions(config, lease) == {}

    def test_lease_json_has_the_forwards(self, capsys):
        self._describe_lease(lease_config(forwards_lease()), output="json")
        data = json.loads(capsys.readouterr().out)
        assert data["forward_statuses"][0] == {"name": "bt", "state": "Connected", "message": None}

    def test_lease_without_forwards_has_no_forwards_section(self, capsys):
        config = lease_config(Lease.from_protobuf(members_lease_pb()))
        self._describe_lease(config)
        assert "Forwards" not in capsys.readouterr().out
        config.get_exporter.assert_not_called()

    def _describe_template(self, template, output=None):
        config = Mock()
        config.get_lease_template.return_value = template
        assert describe_lease_template.callback is not None
        inspect.unwrap(describe_lease_template.callback)(config=config, name="ivi", output=output)

    def test_lease_template_shows_its_forwards(self, capsys):
        template = LeaseTemplate.from_protobuf(
            template_pb(forwards=[forward_pb(), forward_pb("console", "headunit.console", "phone.c", "ecu.c")])
        )
        self._describe_template(template)
        out = capsys.readouterr().out
        assert "Forwards:" in out
        assert "bt       headunit.rootcanal, phone.controller" in out
        assert "console  headunit.console, phone.c, ecu.c" in out

    def test_lease_template_json(self, capsys):
        self._describe_template(LeaseTemplate.from_protobuf(template_pb(forwards=[forward_pb()])), output="json")
        assert json.loads(capsys.readouterr().out)["forwards"][0]["name"] == "bt"

    def test_lease_template_without_forwards_is_unchanged(self, capsys):
        self._describe_template(LeaseTemplate.from_protobuf(template_pb()))
        assert "Forwards" not in capsys.readouterr().out

    def test_get_lease_templates_shows_the_forward_names(self, capsys):
        config = Mock()
        config.list_lease_templates.return_value = LeaseTemplateList(
            lease_templates=[LeaseTemplate.from_protobuf(template_pb(forwards=[forward_pb()]))], next_page_token=None
        )
        assert get_lease_templates.callback is not None
        inspect.unwrap(get_lease_templates.callback)(config=config, output=None, page_size=100)
        out = capsys.readouterr().out
        assert "FORWARDS" in out and "bt" in out

    def _describe_exporter(self, exporter, output=None):
        config = Mock()
        config.get_exporter.return_value = exporter
        config.list_leases.return_value = Mock(leases=[])
        assert describe_exporter.callback is not None
        inspect.unwrap(describe_exporter.callback)(config=config, name=exporter.name, output=output)

    def test_exporter_shows_its_ports(self, capsys):
        exporter = Exporter.from_protobuf(
            exporter_pb(ports=[("rootcanal", "provides", "hci-h4"), ("can0", "connects", None, "wired")])
        )
        self._describe_exporter(exporter)
        out = capsys.readouterr().out
        assert "Ports:" in out
        assert "rootcanal  provides   hci-h4    socket      stream" in out
        assert "can0       connects   <none>    wired       stream" in out

    def test_exporter_without_ports_is_unchanged(self, capsys):
        self._describe_exporter(Exporter.from_protobuf(exporter_pb()))
        assert "Ports" not in capsys.readouterr().out
        self._describe_exporter(Exporter.from_protobuf(exporter_pb()), output="json")
        assert "ports" not in json.loads(capsys.readouterr().out)
