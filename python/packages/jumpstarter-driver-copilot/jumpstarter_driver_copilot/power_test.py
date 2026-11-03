from unittest.mock import MagicMock

import pytest

from jumpstarter_driver_copilot.driver import CoPilotGPIO, CoPilotPower

gpiod = pytest.importorskip("gpiod")


@pytest.fixture
def rig(monkeypatch):
    chip = MagicMock()
    monkeypatch.setattr(CoPilotGPIO, "_path_identity", staticmethod(lambda _: (1, 2, 3)))
    monkeypatch.setattr("jumpstarter_driver_copilot.driver.gpiod.Chip", lambda _: chip)
    controller = CoPilotGPIO(device="/dev/fake", managed_lines=[0, 1, 2, 3])
    return CoPilotPower(children={"controller": controller}), controller, chip


def test_native_power_without_hat(rig):
    power, controller, chip = rig
    power.on()
    power.off()
    assert controller.hats == []
    assert chip.request_lines.call_count == 1
    assert [call.args for call in chip.request_lines.return_value.set_value.call_args_list] == [
        (0, gpiod.line.Value.ACTIVE),
        (0, gpiod.line.Value.INACTIVE),
    ]
    assert "power-switch-control" in controller.info()["capabilities"]


def test_state_is_not_electrical_measurement(rig):
    power, _, chip = rig
    chip.request_lines.return_value.get_value.return_value = gpiod.line.Value.ACTIVE
    assert power.state()["switch_enabled"] is True
    assert power.state()["electrical_state_verified"] is False
    with pytest.raises(NotImplementedError, match="measurements"):
        list(power.read())


def test_requires_managed_gpio0(rig):
    power, controller, chip = rig
    controller.managed_lines = [1, 2, 3]
    with pytest.raises(ValueError, match="not managed"):
        power.on()
    chip.request_lines.return_value.set_value.assert_not_called()


def test_reject_inverted_native_supply(rig):
    power, controller, chip = rig
    controller.active_low = True
    with pytest.raises(ValueError, match="active_low"):
        power.off()
    chip.request_lines.return_value.set_value.assert_not_called()


def test_power_hotplug_never_replays_on(rig, monkeypatch):
    power, controller, chip = rig
    power.on()
    monkeypatch.setattr(CoPilotGPIO, "_path_identity", staticmethod(lambda _: (1, 9, 3)))
    chip.request_lines.return_value.get_value.return_value = gpiod.line.Value.INACTIVE
    assert power.state()["switch_enabled"] is False
    assert controller.info()["interrupted_lines"] == [0]
    with pytest.raises(RuntimeError, match="disconnected during an action"):
        power.on()
    with pytest.raises(RuntimeError, match="action interrupted"):
        power.off()
    assert controller.info()["interrupted_lines"] == []


def test_standard_power_rpc(rig):
    from jumpstarter.common.utils import serve

    power, _, chip = rig
    with serve(power) as client:
        client.on()
        client.off()
        assert client.state()["electrical_state_verified"] is False
        chip.request_lines.return_value.set_value.assert_called_with(0, gpiod.line.Value.INACTIVE)
