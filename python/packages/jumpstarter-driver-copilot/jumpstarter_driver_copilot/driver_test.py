from unittest.mock import MagicMock

import pytest
from jumpstarter_driver_gpiod.client import PinState

from jumpstarter_driver_copilot.driver import CoPilotGPIO, CoPilotOutput

from jumpstarter.common.utils import serve
from jumpstarter.config.exporter import ExporterConfigV1Alpha1DriverInstance

gpiod = pytest.importorskip("gpiod")


@pytest.fixture(autouse=True)
def fake_device_identity(monkeypatch):
    monkeypatch.setattr(CoPilotGPIO, "_path_identity", staticmethod(lambda path: (1, 2, 3)))


@pytest.fixture
def gpio(monkeypatch):
    chip = MagicMock()
    chip_factory = MagicMock(return_value=chip)
    monkeypatch.setattr("jumpstarter_driver_copilot.driver.gpiod.Chip", chip_factory)
    controller = CoPilotGPIO(device="/dev/fake", managed_lines=[1, 2, 3])
    return controller, chip, chip_factory


def test_output_reuses_existing_gpio_request(gpio):
    controller, chip, chip_factory = gpio
    output = CoPilotOutput(line=2, children={"controller": controller})
    output.on()
    output.off()
    assert chip_factory.call_count == chip.request_lines.call_count == 1
    assert chip.request_lines.return_value.set_value.call_args_list == [
        ((2, gpiod.line.Value.ACTIVE),),
        ((2, gpiod.line.Value.INACTIVE),),
    ]
    assert output.client() == "jumpstarter_driver_gpiod.client.DigitalOutputClient"


@pytest.mark.parametrize(
    "value, expected", [(gpiod.line.Value.ACTIVE, PinState.ACTIVE), (gpiod.line.Value.INACTIVE, PinState.INACTIVE)]
)
def test_standard_read(gpio, value, expected):
    controller, chip, _ = gpio
    chip.request_lines.return_value.get_value.return_value = value
    output = CoPilotOutput(line=3, children={"controller": controller})
    assert output.read_pin() == expected


def test_unmanaged_line_cannot_be_written(gpio):
    controller, chip, _ = gpio
    output = CoPilotOutput(line=4, children={"controller": controller})
    with pytest.raises(ValueError, match="not managed"):
        output.on()
    chip.request_lines.return_value.set_value.assert_not_called()


def test_missing_controller():
    with pytest.raises(ValueError, match="children.controller"):
        CoPilotOutput(line=1)


@pytest.mark.parametrize("line", [-1, 1.5, True, "1"])
def test_invalid_line(gpio, line):
    controller, _, _ = gpio
    with pytest.raises(ValueError, match="non-negative integer"):
        CoPilotOutput(line=line, children={"controller": controller})


def test_close_controller_once_through_multiple_outputs(gpio):
    controller, chip, _ = gpio
    outputs = [CoPilotOutput(line=line, children={"controller": controller}) for line in [1, 2, 3]]
    controller.close()
    for output in outputs:
        output.close()
    chip.request_lines.return_value.release.assert_called_once()
    chip.close.assert_called_once()
    with pytest.raises(RuntimeError, match="closed"):
        outputs[0].on()
    before = chip.request_lines.return_value.set_value.call_count
    outputs[0].off()
    assert chip.request_lines.return_value.set_value.call_count == before


def test_close_chip_even_if_release_raises(gpio):
    controller, chip, _ = gpio
    chip.request_lines.return_value.release.side_effect = OSError("release failed")
    with pytest.raises(OSError):
        controller.close()
    controller.close()
    chip.close.assert_called_once()


def test_request_failure_closes_chip(monkeypatch):
    chip = MagicMock()
    chip.request_lines.side_effect = OSError("busy")
    monkeypatch.setattr("jumpstarter_driver_copilot.driver.gpiod.Chip", MagicMock(return_value=chip))
    with pytest.raises(OSError, match="busy"):
        CoPilotGPIO(device="/dev/fake")
    chip.close.assert_called_once()


@pytest.mark.parametrize("duration", [0, 31, float("nan"), float("inf")])
def test_pulse_duration_is_finite(gpio, duration):
    controller, chip, _ = gpio
    with pytest.raises(ValueError, match="between 1 and 30"):
        controller.pulse(1, duration)
    chip.request_lines.return_value.set_value.assert_not_called()


def test_real_jumpstarter_config_resolves_shared_controller(monkeypatch):
    chip = MagicMock()
    factory = MagicMock(return_value=chip)
    monkeypatch.setattr("jumpstarter_driver_copilot.driver.gpiod.Chip", factory)
    config = ExporterConfigV1Alpha1DriverInstance.model_validate(
        {
            "children": {
                "copilot": {
                    "type": "jumpstarter_driver_copilot.driver.CoPilotGPIO",
                    "config": {"device": "/dev/fake", "managed_lines": [1, 2, 3]},
                },
                "power": {
                    "type": "jumpstarter_driver_copilot.driver.CoPilotOutput",
                    "config": {"line": 1},
                    "children": {"controller": {"ref": "copilot"}},
                },
                "reset": {
                    "type": "jumpstarter_driver_copilot.driver.CoPilotOutput",
                    "config": {"line": 3},
                    "children": {"controller": {"ref": "copilot"}},
                },
            }
        }
    )
    root = config.instantiate()
    root.enumerate()
    assert isinstance(root.children["power"], CoPilotOutput)
    root.children["power"].on()
    assert isinstance(root.children["reset"], CoPilotOutput)
    root.children["reset"].off()
    assert factory.call_count == chip.request_lines.call_count == 1
    chip.request_lines.return_value.set_value.assert_any_call(1, gpiod.line.Value.ACTIVE)
    root.close()
    chip.request_lines.return_value.release.assert_called_once()
    chip.close.assert_called_once()


def test_standard_client_over_real_jumpstarter_rpc(gpio):
    controller, chip, _ = gpio
    output = CoPilotOutput(line=2, children={"controller": controller})
    with serve(output) as client:
        client.on()
        chip.request_lines.return_value.set_value.assert_called_with(2, gpiod.line.Value.ACTIVE)
        chip.request_lines.return_value.get_value.return_value = gpiod.line.Value.ACTIVE
        assert client.read() == PinState.ACTIVE
        client.off()
        chip.request_lines.return_value.get_value.return_value = gpiod.line.Value.INACTIVE
        assert client.read() == PinState.INACTIVE


@pytest.mark.parametrize(
    "config",
    [
        {"managed_lines": []},
        {"managed_lines": [1, 1]},
        {"managed_lines": [-1]},
        {"managed_lines": [True]},
        {"managed_lines": [1.5]},
        {"managed_lines": ["1"]},
        {"serial": "../other"},
        {"serial": "/dev/gpiochip0"},
        {"serial": ""},
        {"serial": "selected", "device": "/dev/fake"},
        {"hats": [{"type": "unknown"}]},
        {"hats": ["triple-relay"]},
        {"hats": [{"type": "triple-relay", "relay_lines": [1, 1, 2]}]},
        {"hats": [{"type": "triple-relay", "relay_lines": [True, 2, 3]}]},
        {"hats": [{"type": "triple-relay", "relay_lines": [1, 2, 8]}]},
        {"hats": [{"type": "usb-extension", "ports": True}]},
        {"hats": [{"type": "triple-relay"}, {"type": "triple-relay"}]},
    ],
)
def test_invalid_configuration_does_not_open_gpio(monkeypatch, config):
    factory = MagicMock()
    monkeypatch.setattr("jumpstarter_driver_copilot.driver.gpiod.Chip", factory)
    with pytest.raises((ValueError, TypeError)):
        CoPilotGPIO(**config)
    factory.assert_not_called()


@pytest.mark.parametrize("active", [1, 0, "false", None])
def test_invalid_state_does_not_write(gpio, active):
    controller, chip, _ = gpio
    with pytest.raises(TypeError, match="boolean"):
        controller.set_line(1, active)
    chip.request_lines.return_value.set_value.assert_not_called()


@pytest.mark.parametrize("value", [True, "1", None])
def test_invalid_pulse_type_does_not_write(gpio, value):
    controller, chip, _ = gpio
    with pytest.raises(ValueError, match="duration"):
        controller.pulse(1, value)
    chip.request_lines.return_value.set_value.assert_not_called()


def test_missing_gpiod_reports_requirement(monkeypatch):
    monkeypatch.setattr("jumpstarter_driver_copilot.driver.gpiod", None)
    with pytest.raises(ImportError, match="requires Linux"):
        CoPilotGPIO(device="/dev/fake")


def test_controller_rpc_and_session_cleanup(gpio):
    controller, chip, _ = gpio
    with serve(controller) as client:
        assert client.info()["connected"]
        client.set_line(1, True)
        chip.request_lines.return_value.set_value.assert_called_with(1, gpiod.line.Value.ACTIVE)
    chip.request_lines.return_value.release.assert_called_once()
    chip.close.assert_called_once()
    assert controller._closed


def test_invalid_line_settings_close_chip(monkeypatch):
    chip = MagicMock()
    monkeypatch.setattr("jumpstarter_driver_copilot.driver.gpiod.Chip", lambda _: chip)
    monkeypatch.setattr(
        "jumpstarter_driver_copilot.driver.gpiod.LineSettings", MagicMock(side_effect=ValueError("settings"))
    )
    with pytest.raises(ValueError, match="settings"):
        CoPilotGPIO(device="/dev/fake")
    chip.close.assert_called_once()


@pytest.mark.parametrize("config", [{"active_low": "false"}, {"initial_active": 1}])
def test_invalid_boolean_configuration_never_opens_gpio(monkeypatch, config):
    factory = MagicMock()
    monkeypatch.setattr("jumpstarter_driver_copilot.driver.gpiod.Chip", factory)
    with pytest.raises(TypeError, match="booleans"):
        CoPilotGPIO(device="/dev/fake", **config)
    factory.assert_not_called()


def test_packaged_example_resolves_shared_controller(monkeypatch):
    from pathlib import Path

    import yaml

    chip = MagicMock()
    factory = MagicMock(return_value=chip)
    monkeypatch.setattr("jumpstarter_driver_copilot.driver.gpiod.Chip", factory)
    monkeypatch.setattr(CoPilotGPIO, "_resolve_device", lambda _: "/dev/fake")
    example = Path(__file__).parents[1] / "examples/exporter.yaml"
    data = yaml.safe_load(example.read_text())
    config = ExporterConfigV1Alpha1DriverInstance.model_validate({"children": data["export"]})
    with serve(config.instantiate()) as client:
        client.power.on()
        client.reset.on()
        client.reset.off()
        client.power.off()
    assert factory.call_count == 1
    chip.close.assert_called_once()
