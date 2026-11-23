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
from jumpstarter_cli.describe import describe_lease, describe_lease_template
from jumpstarter_cli.get import get_lease_templates, get_leases
from jumpstarter_cli.jmp import jmp
from jumpstarter_cli.members import MemberRequest, parse_member_request
from jumpstarter_cli.mobly import ADB_DEVICE_CLIENT, build_testbed, export_mobly_testbed, find_adb_devices
from jumpstarter_cli.update import update_lease

from jumpstarter.client.exceptions import ControllerTooOldError
from jumpstarter.client.grpc import Lease, LeaseConstraint, LeaseMember, LeaseTemplate, LeaseTemplateList
from jumpstarter.client.grpc_members_test import members_lease_pb, template_pb
from jumpstarter.config.env import JMP_LEASE, JMP_LEASE_MEMBERS


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


def template(name="ivi") -> LeaseTemplate:
    return LeaseTemplate.from_protobuf(template_pb(name))


def create(config, **kwargs):
    defaults = {
        "config": config,
        "selector": None,
        "exporter_name": None,
        "duration": None,
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


class TestParseMemberRequest:
    def test_no_member_option_is_a_request_for_one_exporter(self):
        assert parse(selector="a=b") is None
        assert parse() is None

    def test_template_with_without_and_member(self):
        request = parse(lease_template="ivi", without=("phone",), member_entries=("headunit=soc=sa8775p,rev!=a",))
        assert request == MemberRequest(
            template="ivi", excluded_members=["phone"], member_selectors={"headunit": "soc=sa8775p,rev!=a"}
        )
        assert request.create_kwargs() == {
            "template": "ivi",
            "excluded_members": ["phone"],
            "member_selectors": {"headunit": "soc=sa8775p,rev!=a"},
        }
        assert parse(lease_template="ivi").create_kwargs() == {"template": "ivi"}

    def test_ad_hoc_members_with_same_label(self):
        request = parse(
            member_entries=("phone=device-type=phone", "headunit=device-type=headunit"),
            same_label_entries=("rf-domain=phone,headunit",),
        )
        assert request.template is None
        assert request.members == [
            LeaseMember(name="phone", selector="device-type=phone"),
            LeaseMember(name="headunit", selector="device-type=headunit"),
        ]
        assert request.constraints == [LeaseConstraint(member_names=["phone", "headunit"], same_label="rf-domain")]
        assert set(request.create_kwargs()) == {"members", "constraints"}

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            # DD-12: the options for one exporter do not name a role
            ({"lease_template": "ivi", "selector": "a=b"}, "--selector/-l cannot be used with --lease-template/-t"),
            ({"lease_template": "ivi", "exporter_name": "e"}, "--name/-n cannot be used with --lease-template/-t"),
            ({"member_entries": ("a=b=c",), "selector": "a=b"}, "--selector/-l cannot be used with --member"),
            ({"member_entries": ("a=b=c",), "exporter_name": "e"}, "--name/-n cannot be used with --member"),
            ({"lease_template": "ivi", "allow_disabled": True}, "--allow-disabled cannot be used with"),
            # options that need the other form
            ({"without": ("phone",)}, "--without needs --lease-template/-t"),
            ({"without": ("phone",), "selector": "a=b"}, "--without needs --lease-template/-t"),
            ({"without": ("phone",), "member_entries": ("a=b=c",)}, "--without needs --lease-template/-t"),
            ({"same_label_entries": ("k=a,b",)}, "--same-label needs --member"),
            (
                {"lease_template": "ivi", "same_label_entries": ("k=a,b",)},
                "--same-label cannot be used with --lease-template/-t",
            ),
            # formats
            ({"member_entries": ("phone",)}, "Invalid --member format"),
            ({"member_entries": ("phone=",)}, "Invalid --member format"),
            ({"member_entries": ("Phone=a=b",)}, "invalid role name 'Phone'"),
            ({"member_entries": ("a=b=c", "a=d=e")}, "the role 'a' is given more than once"),
            ({"lease_template": "ivi", "without": ("-x",)}, "invalid role name '-x'"),
            (
                {"lease_template": "ivi", "without": ("phone",), "member_entries": ("phone=a=b",)},
                "the role 'phone' is given to both --without and --member",
            ),
            ({"member_entries": ("a=b=c", "d=e=f"), "same_label_entries": ("bus",)}, "Invalid --same-label format"),
            ({"member_entries": ("a=b=c", "d=e=f"), "same_label_entries": ("bus=a",)}, "two or more different roles"),
            ({"member_entries": ("a=b=c", "d=e=f"), "same_label_entries": ("bus=a,a",)}, "two or more different"),
            (
                {"member_entries": ("a=b=c", "d=e=f"), "same_label_entries": ("bus=a,x",)},
                "no --member defines the role 'x'",
            ),
            ({"member_entries": tuple(f"r{i}=a=b" for i in range(9))}, "Too many --member entries \\(max 8\\)"),
        ],
    )
    def test_rejected_combinations(self, kwargs, message):
        with pytest.raises(click.UsageError, match=message):
            parse(**kwargs)

    def test_template_check_rejects_unknown_and_required_roles(self):
        with pytest.raises(click.UsageError, match="has no role 'nope' \\(roles: headunit, phone\\*, ecu\\)"):
            parse(lease_template="ivi", member_entries=("nope=a=b",)).check_template(template())
        with pytest.raises(click.UsageError, match="--without nope: .* has no role 'nope'"):
            parse(lease_template="ivi", without=("nope",)).check_template(template())
        with pytest.raises(click.UsageError, match="the role 'headunit' is required"):
            parse(lease_template="ivi", without=("headunit",)).check_template(template())
        parse(lease_template="ivi", without=("phone",), member_entries=("ecu=a=b",)).check_template(template())


class TestCreateLease:
    def _config(self):
        config = Mock()
        config.get_lease_template.return_value = template()
        config.create_lease.return_value = Mock(deprecated_labels={})
        return config

    def test_template_request(self):
        config = self._config()
        create(
            config,
            lease_template="ivi",
            without=("phone",),
            member_entries=("headunit=soc=sa8775p",),
            duration=timedelta(hours=4),
            share_with="lab-support",
            tags=("build=1",),
        )
        config.get_lease_template.assert_called_once_with("ivi")
        config.create_lease.assert_called_once_with(
            duration=timedelta(hours=4),
            begin_time=None,
            lease_id=None,
            tags={"build": "1"},
            context=None,
            shared_with=["lab-support"],
            template="ivi",
            excluded_members=["phone"],
            member_selectors={"headunit": "soc=sa8775p"},
        )

    def test_template_default_duration_makes_duration_optional(self):
        config = self._config()
        create(config, lease_template="ivi")
        assert config.create_lease.call_args.kwargs["duration"] is None

    def test_template_without_default_duration_needs_duration(self):
        config = self._config()
        definition = template_pb()
        definition.ClearField("default_duration")
        config.get_lease_template.return_value = LeaseTemplate.from_protobuf(definition)
        with pytest.raises(click.UsageError, match="--duration is required: the lease template 'ivi' has no default"):
            create(config, lease_template="ivi")
        config.create_lease.assert_not_called()

    def test_template_role_errors_create_no_lease(self):
        config = self._config()
        with pytest.raises(click.UsageError, match="the role 'headunit' is required"):
            create(config, lease_template="ivi", without=("headunit",))
        config.create_lease.assert_not_called()

    def test_ad_hoc_members(self):
        config = self._config()
        create(
            config,
            member_entries=("phone=device-type=phone", "headunit=device-type=headunit"),
            same_label_entries=("rf-domain=phone,headunit",),
            duration=timedelta(minutes=45),
        )
        config.get_lease_template.assert_not_called()
        kwargs = config.create_lease.call_args.kwargs
        assert [m.name for m in kwargs["members"]] == ["phone", "headunit"]
        assert kwargs["constraints"][0].same_label == "rf-domain"
        assert "selector" not in kwargs and "template" not in kwargs

    def test_ad_hoc_members_and_scalar_need_duration(self):
        for kwargs in ({"member_entries": ("phone=a=b",)}, {"selector": "a=b"}):
            with pytest.raises(click.MissingParameter):
                create(self._config(), **kwargs)

    def test_selector_and_name_are_rejected_with_template(self):
        with pytest.raises(click.UsageError, match="--selector/-l cannot be used with --lease-template/-t"):
            create(self._config(), lease_template="ivi", selector="a=b")

    def test_command_line(self):
        result = CliRunner().invoke(jmp, ["create", "lease", "-t", "ivi", "-n", "exp", "--duration", "1h"])
        assert result.exit_code == 2
        assert "--name/-n cannot be used with --lease-template/-t" in result.output
        result = CliRunner().invoke(jmp, ["create", "lease", "--without", "phone", "-l", "a=b", "--duration", "1h"])
        assert result.exit_code == 2 and "--without needs --lease-template/-t" in result.output

    def test_old_controller_message(self):
        config = self._config()
        config.get_lease_template.side_effect = ControllerTooOldError(
            "the controller is too old for lease templates: upgrade the controller"
        )
        with pytest.raises(ControllerTooOldError, match="too old for lease templates"):
            create(config, lease_template="ivi")


class TestGetLeaseTemplates:
    def test_table(self):
        config = Mock()
        config.list_lease_templates.return_value = LeaseTemplateList(
            lease_templates=[template("ivi"), template("projection")], next_page_token=None
        )
        assert get_lease_templates.callback is not None
        with patch("jumpstarter_cli.get.model_print") as model_print:
            inspect.unwrap(get_lease_templates.callback)(config=config, output=None, page_size=50)
        config.list_lease_templates.assert_called_once_with(page_size=50)
        model_print.assert_called_once_with(config.list_lease_templates.return_value, None)

    @pytest.mark.parametrize("output", ["json", "yaml", "name", None])
    def test_outputs(self, output, capsys):
        config = Mock()
        config.list_lease_templates.return_value = LeaseTemplateList(lease_templates=[template()], next_page_token=None)
        assert get_lease_templates.callback is not None
        inspect.unwrap(get_lease_templates.callback)(config=config, output=output, page_size=100)
        out = capsys.readouterr().out
        if output == "json":
            data = json.loads(out)
            assert data["lease_templates"][0]["members"][1]["member"]["optional"] is True
            assert data["lease_templates"][0]["default_duration"] == 7200.0
        elif output == "yaml":
            assert yaml.safe_load(out)["lease_templates"][0]["name"] == "ivi"
        elif output == "name":
            assert out.strip() == "ivi"
        else:
            assert "headunit, phone*, ecu" in out and "2/3" in out and "AVAILABLE" in out

    def test_aliases(self):
        runner = CliRunner()
        plural = runner.invoke(jmp, ["get", "lease-templates", "--help"])
        assert plural.exit_code == 0
        assert runner.invoke(jmp, ["get", "lease-template", "--help"]).output == plural.output
        assert runner.invoke(jmp, ["describe", "lease-templates", "--help"]).exit_code == 0


class TestDescribe:
    def test_lease_template(self, capsys):
        config = Mock()
        config.get_lease_template.return_value = template()
        assert describe_lease_template.callback is not None
        inspect.unwrap(describe_lease_template.callback)(config=config, name="ivi", output=None)
        out = capsys.readouterr().out
        config.get_lease_template.assert_called_once_with("ivi")
        for expected in (
            "Name:                 ivi",
            "AAOS head unit, phone, and body ECU on one CAN bus",
            "https://wiki.example.com/labs/ivi.png",
            "  https://wiki.example.com/labs/ivi",
            "Instructions:\n  Set the head unit to on before you start the phone.",
            "2/3 members",
            "2:00:00",
            "lab-support",
            "phone*",
            "device-type=aaos-headunit",
            "exporter ecu-11",
            "headunit, ecu",
            "bus",
        ):
            assert expected in out, expected

    def test_lease_template_json(self, capsys):
        config = Mock()
        config.get_lease_template.return_value = template()
        assert describe_lease_template.callback is not None
        inspect.unwrap(describe_lease_template.callback)(config=config, name="ivi", output="json")
        data = json.loads(capsys.readouterr().out)
        assert data["name"] == "ivi" and data["constraints"] == [
            {"member_names": ["headunit", "ecu"], "same_label": "bus"}
        ]

    def test_lease_with_members(self, capsys):
        config = Mock()
        config.get_lease.return_value = Lease.from_protobuf(members_lease_pb(template="ivi"))
        assert describe_lease.callback is not None
        inspect.unwrap(describe_lease.callback)(config=config, name="lease-9m2qd", show_drivers=False, output=None)
        out = capsys.readouterr().out
        assert "Template:" in out and "ivi" in out
        assert "Selector:" not in out
        assert "rack3-phone-4" in out and "Bound" in out and "phone*" in out
        assert "rf-domain" in out


class TestGetLeases:
    def _get(self, config, **kwargs):
        defaults = {
            "config": config,
            "selector": None,
            "output": None,
            "show_all": False,
            "all_clients": False,
            "tag_filter": None,
            "page_size": 100,
            "name": None,
        }
        assert get_leases.callback is not None
        inspect.unwrap(get_leases.callback)(**{**defaults, **kwargs})

    def test_lease_shows_exporter_of_each_role_and_template(self, capsys):
        config = Mock()
        config.get_lease.return_value = Lease.from_protobuf(members_lease_pb(template="ivi"))
        self._get(config, name="lease-9m2qd")
        out = capsys.readouterr().out
        assert "phone=rack3-phone-4," in out and "headunit=rack3-hu-2" in out
        assert "TEMPLATE" in out and "ivi" in out

    def test_mobly_needs_a_name_members_and_the_shell(self, monkeypatch):
        config = Mock()
        with pytest.raises(click.UsageError, match="-o mobly needs the NAME of a lease"):
            self._get(config, output="mobly")
        scalar = Lease.from_protobuf(members_lease_pb())
        scalar.members = []
        config.get_lease.return_value = scalar
        with pytest.raises(click.UsageError, match="-o mobly needs a lease with members"):
            self._get(config, name="one", output="mobly")
        config.get_lease.return_value = Lease.from_protobuf(members_lease_pb())
        monkeypatch.delenv(JMP_LEASE_MEMBERS, raising=False)
        with pytest.raises(click.UsageError, match="run it in 'jmp shell --lease lease-9m2qd'"):
            self._get(config, name="lease-9m2qd", output="mobly")
        monkeypatch.setenv(JMP_LEASE_MEMBERS, "{}")
        monkeypatch.setenv(JMP_LEASE, "another")
        with pytest.raises(click.UsageError, match="run it in 'jmp shell --lease lease-9m2qd'"):
            self._get(config, name="lease-9m2qd", output="mobly")

    def test_mobly_output_is_the_testbed_alone(self, capsys):
        config = Mock()
        config.get_lease.return_value = Lease.from_protobuf(members_lease_pb())
        with patch("jumpstarter_cli.get.export_mobly_testbed", return_value="TestBeds: []\n") as export:
            self._get(config, name="lease-9m2qd", output="mobly")
        export.assert_called_once_with("lease-9m2qd")
        assert capsys.readouterr().out == "TestBeds: []\n"


def fake_client(label="x.Composite", **children):
    return Mock(labels={"jumpstarter.dev/client": label}, children=children)


class TestMobly:
    def test_find_adb_devices(self):
        tree = fake_client(
            power=fake_client("jumpstarter_driver_power.client.PowerClient"),
            dut=fake_client(
                adb=fake_client(ADB_DEVICE_CLIENT), server=fake_client("jumpstarter_driver_adb.client.AdbClient")
            ),
        )
        assert find_adb_devices(tree) == [("dut", "adb")]

    def test_testbed_shape(self):
        assert build_testbed("lease-9m2qd", [("phone", "127.0.0.1:5601")]) == {
            "TestBeds": [
                {
                    "Name": "lease-9m2qd",
                    "Controllers": {"AndroidDevice": [{"serial": "127.0.0.1:5601", "label": "phone"}]},
                }
            ]
        }

    def _export(self, monkeypatch, by_role):
        from contextlib import contextmanager

        @contextmanager
        def env_members():
            yield Mock(by_role=by_role)

        monkeypatch.setenv(JMP_LEASE_MEMBERS, "{}")
        monkeypatch.setenv(JMP_LEASE, "lease-9m2qd")
        with patch("jumpstarter_cli.mobly.env_members", env_members):
            return export_mobly_testbed("lease-9m2qd")

    def test_one_android_device_for_each_member_with_adb(self, monkeypatch, capsys):
        text = self._export(
            monkeypatch,
            {
                "phone": fake_client(adb=fake_client(ADB_DEVICE_CLIENT)),
                "headunit": fake_client(dut=fake_client(adb=fake_client(ADB_DEVICE_CLIENT))),
                "ecu": fake_client(power=fake_client("jumpstarter_driver_power.client.PowerClient")),
                "tablet": None,
            },
        )
        [testbed] = yaml.safe_load(text)["TestBeds"]
        assert testbed["Name"] == "lease-9m2qd"
        devices = testbed["Controllers"]["AndroidDevice"]
        assert [d["label"] for d in devices] == ["phone", "headunit"]
        assert all(d["serial"].startswith("127.0.0.1:") for d in devices)
        captured = capsys.readouterr()
        assert captured.out == ""
        phone_port = devices[0]["serial"].split(":")[1]
        headunit_port = devices[1]["serial"].split(":")[1]
        assert f"j phone adb connect -P {phone_port} &" in captured.err
        assert f"j headunit dut adb connect -P {headunit_port} &" in captured.err

    def test_no_member_with_adb(self, monkeypatch):
        with pytest.raises(click.ClickException, match="no member of lease lease-9m2qd has an ADB device driver"):
            self._export(monkeypatch, {"ecu": fake_client()})


class TestUpdateLease:
    def test_update_works_by_name_for_a_lease_with_members(self, capsys):
        config = Mock()
        config.metadata.namespace = "default"
        config.update_lease.return_value = Lease.from_protobuf(members_lease_pb(template="ivi"))
        assert update_lease.callback is not None
        inspect.unwrap(update_lease.callback)(
            config=config,
            name="lease-9m2qd",
            duration=timedelta(hours=1),
            begin_time=None,
            to_client="other",
            share_add=("lab-support",),
            share_remove=(),
            output=None,
        )
        config.update_lease.assert_called_once_with(
            "lease-9m2qd",
            duration=timedelta(hours=1),
            begin_time=None,
            client="namespaces/default/clients/other",
            add_shared_with=["lab-support"],
            remove_shared_with=None,
        )
        assert "headunit=rack3-hu-2" in capsys.readouterr().out
