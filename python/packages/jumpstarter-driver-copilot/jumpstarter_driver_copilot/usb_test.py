from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest
import yaml
from click.testing import CliRunner

from jumpstarter_driver_copilot.client import CoPilotUSBClient
from jumpstarter_driver_copilot.driver import CoPilotGPIO, CoPilotUSB

from jumpstarter.common.utils import serve
from jumpstarter.config.exporter import ExporterConfigV1Alpha1DriverInstance

gpiod = pytest.importorskip("gpiod")


@pytest.fixture(autouse=True)
def fake_device_identity(monkeypatch):
    monkeypatch.setattr(CoPilotGPIO, "_path_identity", staticmethod(lambda path: (1, 2, 3)))


@pytest.fixture
def rig(monkeypatch):
    chip = MagicMock()
    monkeypatch.setattr("jumpstarter_driver_copilot.driver.gpiod.Chip", lambda _: chip)
    controller = CoPilotGPIO(
        device="/dev/fake",
        managed_lines=[0, 1, 2, 3],
        hats=[{"type": "usb-extension", "ports": 3, "usb_lines": [3, 1, 2]}],
    )
    return CoPilotUSB(children={"controller": controller}), controller, chip


def writes(chip):
    return [call.args for call in chip.request_lines.return_value.set_value.call_args_list]


def test_ports_follow_usb_lines_order(rig):
    usb, controller, chip = rig
    assert usb.ports() == 3
    usb.on(1)
    usb.off(1)
    usb.on(3)
    assert writes(chip) == [
        (3, gpiod.line.Value.ACTIVE),
        (3, gpiod.line.Value.INACTIVE),
        (2, gpiod.line.Value.ACTIVE),
    ]
    assert "usb-port-control" in controller.info()["capabilities"]


def test_read_reports_commanded_state(rig):
    usb, _, chip = rig
    chip.request_lines.return_value.get_value.return_value = gpiod.line.Value.ACTIVE
    assert usb.read(2) is True
    chip.request_lines.return_value.get_value.assert_called_with(1)


def test_cycle_turns_off_waits_then_on(rig, monkeypatch):
    usb, _, chip = rig
    sleeps = []
    monkeypatch.setattr("jumpstarter_driver_copilot.driver.time.sleep", sleeps.append)
    usb.cycle(1, 3)
    assert writes(chip) == [(3, gpiod.line.Value.INACTIVE), (3, gpiod.line.Value.ACTIVE)]
    assert sleeps == [3.0]


@pytest.mark.parametrize("wait", [0, 31, float("nan"), True, "2"])
def test_cycle_wait_is_bounded(rig, wait):
    usb, _, chip = rig
    with pytest.raises(ValueError, match="wait"):
        usb.cycle(1, wait)
    chip.request_lines.return_value.set_value.assert_not_called()


@pytest.mark.parametrize("port", [0, 4, 1.5, True])
def test_invalid_port_does_not_write(rig, port):
    usb, _, chip = rig
    with pytest.raises(ValueError, match="port"):
        usb.on(port)
    chip.request_lines.return_value.set_value.assert_not_called()


def test_inventory_only_hat_has_no_port_control(monkeypatch):
    monkeypatch.setattr("jumpstarter_driver_copilot.driver.gpiod.Chip", lambda _: MagicMock())
    controller = CoPilotGPIO(device="/dev/fake", hats=[{"type": "usb-extension", "ports": 3}])
    usb = CoPilotUSB(children={"controller": controller})
    assert "usb-port-control" not in controller.info()["capabilities"]
    with pytest.raises(ValueError, match="usb_lines"):
        usb.on(1)


def test_missing_controller():
    with pytest.raises(ValueError, match="children.controller"):
        CoPilotUSB()


@pytest.mark.parametrize(
    "hats",
    [
        [{"type": "usb-extension", "usb_lines": []}],
        [{"type": "usb-extension", "usb_lines": 1}],
        [{"type": "usb-extension", "usb_lines": [1, 1]}],
        [{"type": "usb-extension", "usb_lines": [True, 2]}],
        [{"type": "usb-extension", "usb_lines": [1, 8]}],
        [{"type": "usb-extension", "ports": 3, "usb_lines": [1, 2]}],
        [{"type": "usb-extension", "usb_lines": [1]}, {"type": "usb-extension", "usb_lines": [2]}],
        [{"type": "usb-extension", "usb_lines": [1]}, {"type": "triple-relay", "relay_lines": [1, 2, 3]}],
    ],
)
def test_invalid_usb_configuration_does_not_open_gpio(monkeypatch, hats):
    factory = MagicMock()
    monkeypatch.setattr("jumpstarter_driver_copilot.driver.gpiod.Chip", factory)
    with pytest.raises((ValueError, TypeError)):
        CoPilotGPIO(device="/dev/fake", managed_lines=[0, 1, 2, 3], hats=hats)
    factory.assert_not_called()


def test_client_over_real_jumpstarter_rpc(rig, monkeypatch):
    usb, _, chip = rig
    monkeypatch.setattr("jumpstarter_driver_copilot.driver.time.sleep", lambda _: None)
    with serve(usb) as client:
        assert client.ports() == 3
        client.on(2)
        chip.request_lines.return_value.set_value.assert_called_with(1, gpiod.line.Value.ACTIVE)
        chip.request_lines.return_value.get_value.return_value = gpiod.line.Value.ACTIVE
        assert client.read(2) is True
        client.cycle(3, 1)
        chip.request_lines.return_value.set_value.assert_called_with(2, gpiod.line.Value.ACTIVE)


def test_cli_port_then_action():
    calls = []
    fake = SimpleNamespace(
        description=None,
        methods_description={},
        on=lambda port: calls.append(("on", port)),
        off=lambda port: calls.append(("off", port)),
        read=lambda port: calls.append(("read", port)) or False,
        cycle=lambda port, wait: calls.append(("cycle", port, wait)),
    )
    cli = CoPilotUSBClient.cli(cast(CoPilotUSBClient, fake))
    runner = CliRunner()
    assert runner.invoke(cli, ["2", "on"]).exit_code == 0
    assert runner.invoke(cli, ["2", "off"]).exit_code == 0
    result = runner.invoke(cli, ["3", "read"])
    assert (result.exit_code, result.output) == (0, "off\n")
    assert runner.invoke(cli, ["1", "cycle", "--wait", "3"]).exit_code == 0
    assert runner.invoke(cli, ["1", "cycle"]).exit_code == 0
    assert calls == [("on", 2), ("off", 2), ("read", 3), ("cycle", 1, 3.0), ("cycle", 1, 2.0)]
    assert runner.invoke(cli, ["0", "on"]).exit_code != 0
    assert len(calls) == 5


def test_packaged_usb_example(monkeypatch):
    chip = MagicMock()
    factory = MagicMock(return_value=chip)
    monkeypatch.setattr("jumpstarter_driver_copilot.driver.gpiod.Chip", factory)
    monkeypatch.setattr(CoPilotGPIO, "_resolve_device", lambda _: "/dev/fake")
    example = Path(__file__).parents[1] / "examples/exporter-usb.yaml"
    data = yaml.safe_load(example.read_text())
    config = ExporterConfigV1Alpha1DriverInstance.model_validate({"children": data["export"]})
    with serve(config.instantiate()) as client:
        client.usb.on(1)
        client.usb.off(1)
        client.power.on()
        client.power.off()
    assert factory.call_count == 1
    chip.close.assert_called_once()
